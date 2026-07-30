"""M1: Markdown Source Parser adapter.

Implements the Structured Source Contract (CONTRACT.md). Wraps
markdown-it-py + mdit-py-plugins (GFM table / strikethrough / footnote)
and produces a typed ``MarkdownParseResult`` that downstream consumers
(normalizer / suitability gate / candidate creation / artifact
materialization) consume.

Scope:
  * CommonMark + GFM table + strikethrough + footnote (degraded).
  * Block types: heading, paragraph, list (wrapper), list_item,
    blockquote, table, table_row, table_cell, code_block,
    thematic_break, footnote (degraded).
  * Inline flattening: emphasis / strong / strikethrough / inline_code
    are flattened into parent block text_content.
  * Link safety: protocol whitelist (http/https/mailto); unsafe
    protocols stripped, link text preserved, recorded in
    payload_json.stripped_links (adaptation_notice; document continues).
  * Raw HTML: html_block aggregated, inline HTML stripped, executable
    structure never survives into text (adaptation_notice; document
    continues). Bare unknown tags (``vector<T>`` / ``<name>``) are
    plain-text placeholders and preserved verbatim.
  * Tables: deterministic GFM tables (one header row, consistent raw
    cell counts) freeze as stable; structure-uncertain tables route to
    content check.
  * Diagnostics: structured warnings with a three-level classification
    (silent / adaptation_notice / content_check) + unsupported +
    classification-driven outcome per CONTRACT.md Clause 5.

Hard constraints (per Structured Source Contract CONTRACT.md):
  * Legacy regex normalizer (NORMALIZER_VERSION) is NOT touched here;
    the normalizer module wires this adapter in M1.
  * Content-check conditions (unclosed fences, footnote reference loss,
    table structure uncertainty, missing source ranges) are fail-closed
    → candidate_document_required.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.footnote import footnote_plugin

from app.contracts.annotation import utf16_code_unit_length

# ---------------------------------------------------------------------------
# Identity constants (Clause 1)
# ---------------------------------------------------------------------------

PARSER_NAME = "markdown_it_py"
PARSER_VERSION = "v1"
PROFILE = "commonmark_gfm_v1"

# ---------------------------------------------------------------------------
# Link safety (Clause 3.5)
# ---------------------------------------------------------------------------

SAFE_LINK_PROTOCOLS = frozenset({"http", "https", "mailto"})

# ---------------------------------------------------------------------------
# Authoritative Normalization 三级分类（L1）
#
# Every parser diagnostic is classified into exactly one bucket:
#   * ``silent``            — deterministic, meaning-preserving normalization;
#                             invisible to the user.
#   * ``adaptation_notice`` — content was cleaned / safely downgraded but the
#                             document continues; surfaced as a non-blocking
#                             notice.
#   * ``content_check``     — content, boundaries or meaning may change; the
#                             document routes to candidate review.
# The parser outcome follows the classification: any ``content_check``
# warning forces ``candidate_document_required``; ``silent`` and
# ``adaptation_notice`` warnings never do.
# ---------------------------------------------------------------------------

CLASSIFICATION_SILENT = "silent"
CLASSIFICATION_ADAPTATION_NOTICE = "adaptation_notice"
CLASSIFICATION_CONTENT_CHECK = "content_check"

# Diagnostic messages (Clause 5 — closed set, fixed text).
_MSG_RAW_HTML_BLOCK = (
    "Raw HTML block detected; executable structure removed, text preserved "
    "as a plain paragraph."
)
_MSG_INLINE_HTML = "Inline HTML tag stripped from paragraph text."
_MSG_UNSAFE_LINK = (
    "Links with unsafe protocols (javascript/data/vbscript) were stripped "
    "from paragraph text; link text preserved."
)
_MSG_FOOTNOTE_REF = (
    "Footnote reference encountered; the reference marker is dropped from "
    "body text while the definition is captured as a footnote block."
)
_MSG_UNCLOSED_FENCE = (
    "Fenced code block is missing its closing fence; captured as code_block "
    "but requires candidate review for boundary correctness."
)
_MSG_STRIKETHROUGH = (
    "Strikethrough syntax captured as plain text; rendering is preserved."
)
_MSG_MERMAID = (
    "Mermaid code block is stored as static text; diagram is not "
    "rendered or executed."
)
_MSG_CODE_DOMINANT = (
    "Input is code-dominant with no narrative blocks; rejected from stable "
    "document freeze, action required."
)
_MSG_MISSING_SOURCE_RANGE = (
    "Parser token missing source range; requires candidate review for "
    "boundary correctness."
)
_MSG_TABLE_STRUCTURE_UNCERTAIN = (
    "Table row/column structure does not match the header definition; "
    "cells would be dropped or padded during deterministic normalization."
)
_MSG_UNSUP_RAW_HTML = (
    "Raw HTML is not a first-class block type in the first phase; text is "
    "extracted but structure is not preserved."
)
_MSG_UNSUP_UNSAFE_LINK = (
    "Unsafe-protocol link sanitization is a first-phase safety measure; "
    "full link audit requires candidate review."
)
_MSG_UNSUP_FOOTNOTE = (
    "Footnote definition is captured as a block but full footnote semantics "
    "(multi-ref, backref) are not supported in first phase."
)


def _is_safe_link(href: str) -> bool:
    """Return True if the link protocol is whitelisted (or relative)."""
    if not href:
        return False
    try:
        parsed = urlparse(href)
    except ValueError:
        return False
    scheme = parsed.scheme.lower()
    if not scheme:
        return True  # Relative link / anchor
    return scheme in SAFE_LINK_PROTOCOLS


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SourceRange:
    """1-based line range of a block in the normalized source.

    UTF-16 offsets are deferred (always None in first phase).
    """

    line_start: int
    line_end: int
    utf16_start: int | None = None
    utf16_end: int | None = None


@dataclass(frozen=True, slots=True)
class ParsedBlock:
    """One parsed block from the Markdown source."""

    block_id: str
    block_type: str
    text_content: str | None
    payload_json: dict[str, Any]
    parent_block_id: str | None
    order_index: int
    source_range: SourceRange


@dataclass(frozen=True, slots=True)
class DiagnosticWarning:
    """Structured warning (Clause 5 + L1 three-level classification)."""

    code: str
    message: str
    blocks_freeze: bool
    # silent | adaptation_notice | content_check (see module constants).
    classification: str


@dataclass(frozen=True, slots=True)
class UnsupportedFeature:
    """Structured unsupported feature note (Clause 5)."""

    code: str
    message: str


@dataclass(frozen=True, slots=True)
class MarkdownParseResult:
    """Full parse result from the adapter."""

    parser_name: str
    parser_version: str
    profile: str
    blocks: tuple[ParsedBlock, ...]
    warnings: tuple[DiagnosticWarning, ...]
    unsupported: tuple[UnsupportedFeature, ...]
    # stable_document_ready | candidate_document_required |
    # input_rejected_or_action_required
    outcome: str


class MarkdownSourceParseError(ValueError):
    """Raised when the parser adapter encounters an unrecoverable error."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_newlines(text: str) -> str:
    """Normalize CRLF / CR to LF before parsing (Clause 2)."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _map_to_1based(map_val: list[int] | None) -> SourceRange | None:
    """Convert markdown-it-py 0-based [start, end) to 1-based [start, end]."""
    if map_val is None or len(map_val) != 2:
        return None
    start, end = map_val
    return SourceRange(line_start=start + 1, line_end=end)


def _resolve_range(
    src_range: SourceRange | None,
    flags: _DiagnosticFlags,
) -> SourceRange:
    """Return ``src_range`` or a fail-closed placeholder.

    When ``src_range`` is ``None`` (token has no ``map``), set the
    ``has_missing_source_range`` flag so the diagnostics stage can emit
    a ``missing_source_range`` warning and route the document to
    ``candidate_document_required`` (Clause 2). The placeholder
    ``SourceRange(0, 0)`` is only used internally so block construction
    never sees ``None``; the outcome routing ensures such documents do
    not freeze as stable.
    """
    if src_range is None:
        flags.has_missing_source_range = True
        return SourceRange(0, 0)
    return src_range


def _extract_inline_text(token: Token) -> str:
    """Flatten an inline token's children into plain text.

    Inline marks (emphasis / strong / strikethrough / inline_code /
    link_open / link_close / html_inline) are stripped; their text
    content is preserved.
    """
    if not token.children:
        return token.content or ""

    parts: list[str] = []
    for child in token.children:
        if child.type == "text":
            parts.append(child.content)
        elif child.type == "code_inline":
            parts.append(child.content)
        elif child.type in ("softbreak", "hardbreak"):
            parts.append("\n")
        elif child.type in (
            "link_open", "link_close",
            "em_open", "em_close",
            "strong_open", "strong_close",
            "s_open", "s_close",
            "del_open", "del_close",
            "footnote_ref",
        ):
            continue
        elif child.type == "html_inline":
            # Non-HTML placeholders (vector<T> / <name>) are literal text
            # and must survive every flattening path, including the
            # html_block aggregation path.
            if _is_non_html_placeholder(child.content):
                parts.append(child.content)
            continue
        elif child.type == "image":
            parts.append(child.content)
        else:
            if child.content:
                parts.append(child.content)
    return "".join(parts)


def _reconstruct_raw_with_html(token: Token) -> str:
    """Reconstruct raw inline text including html_inline content.

    This is used to detect unsafe links that markdown-it-py could not
    parse because html_inline tokens broke the link syntax.
    Link_open/close and emphasis marks are skipped; their text children
    are included.
    """
    if not token.children:
        return token.content or ""

    parts: list[str] = []
    for child in token.children:
        if child.type == "text":
            parts.append(child.content)
        elif child.type == "html_inline":
            parts.append(child.content)
        elif child.type == "code_inline":
            parts.append(child.content)
        elif child.type in ("softbreak", "hardbreak"):
            parts.append("\n")
        elif child.type == "image":
            parts.append(child.content)
        elif child.type in (
            "link_open", "link_close",
            "em_open", "em_close",
            "strong_open", "strong_close",
            "s_open", "s_close",
            "del_open", "del_close",
            "footnote_ref",
        ):
            continue
        elif child.content:
            parts.append(child.content)
    return "".join(parts)


def _extract_and_strip_links(text: str) -> tuple[str, list[dict[str, str]], list[dict[str, str]]]:
    """Extract ``[label](href)`` links using bracket-pair matching.

    Returns (cleaned_text, safe_links, unsafe_links) where cleaned_text
    has ``[label](href)`` replaced by ``label``.
    """
    safe_links: list[dict[str, str]] = []
    unsafe_links: list[dict[str, str]] = []
    result: list[str] = []
    i = 0
    length = len(text)

    while i < length:
        if text[i] == "[":
            close_bracket = text.find("]", i + 1)
            if close_bracket == -1:
                result.append(text[i])
                i += 1
                continue
            if close_bracket + 1 >= length or text[close_bracket + 1] != "(":
                result.append(text[i])
                i += 1
                continue
            label = text[i + 1:close_bracket]
            # Find matching ) for ( at close_bracket+1, handling nested ()
            k = close_bracket + 2
            depth = 1
            while k < length and depth > 0:
                if text[k] == "(":
                    depth += 1
                elif text[k] == ")":
                    depth -= 1
                if depth > 0:
                    k += 1
            if depth != 0:
                result.append(text[i])
                i += 1
                continue
            href = text[close_bracket + 2:k]
            if _is_safe_link(href):
                safe_links.append({"text": label, "href": href})
            else:
                unsafe_links.append(
                    {"text": label, "href": href, "reason": "unsafe_protocol"}
                )
            result.append(label)
            i = k + 1
        else:
            result.append(text[i])
            i += 1

    return "".join(result), safe_links, unsafe_links


def _extract_links_from_link_open(
    token: Token,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Extract safe and unsafe links from link_open tokens (parsed by mdit)."""
    safe_links: list[dict[str, str]] = []
    unsafe_links: list[dict[str, str]] = []

    if not token.children:
        return safe_links, unsafe_links

    in_link = False
    current_link_text: list[str] = []
    current_href = ""

    for child in token.children:
        if child.type == "link_open":
            in_link = True
            current_link_text = []
            current_href = ""
            if child.attrs and "href" in child.attrs:
                current_href = str(child.attrs["href"])
        elif child.type == "link_close":
            if in_link:
                link_text = "".join(current_link_text)
                if _is_safe_link(current_href):
                    safe_links.append({"text": link_text, "href": current_href})
                else:
                    unsafe_links.append(
                        {
                            "text": link_text,
                            "href": current_href,
                            "reason": "unsafe_protocol",
                        }
                    )
            in_link = False
        elif in_link:
            if child.type == "text":
                current_link_text.append(child.content)
            elif child.type == "code_inline":
                current_link_text.append(child.content)

    return safe_links, unsafe_links


def _process_paragraph_inline(
    token: Token,
) -> tuple[
    str,
    list[dict[str, Any]],
    list[dict[str, str]],
    list[dict[str, str]],
    bool,
    bool,
]:
    """Process paragraph inline tokens.

    Returns (text, inline_marks, safe_links, unsafe_links, has_inline_html,
    starts_with_html_inline).

    A3 — link safety single-point convergence:
      html_inline + link overlap no longer "rescued" via
      ``_reconstruct_raw_with_html`` + regex re-parse. html_inline is
      detected and recorded as ``inline_html`` warning; the broken link
      syntax is not merged. text_content is the flattened inline text
      with html_inline stripped.
      Non-html_inline unsafe links (javascript:/vbscript:) are still
      categorized via link_open attrs and recorded in ``stripped_links``.
    """
    return _process_inline_with_marks(token)


def _process_inline_with_marks(
    token: Token,
) -> tuple[
    str,
    list[dict[str, Any]],
    list[dict[str, str]],
    list[dict[str, str]],
    bool,
    bool,
]:
    """Process an inline token, extracting text + inline_marks + links.

    Returns (text, inline_marks, safe_links, unsafe_links,
    has_inline_html, starts_with_html_inline).

    inline_marks: list of {type, start, end, [href]} where start/end are
    UTF-16 code unit offsets within the returned text. Marks are emitted
    in close-order (innermost marks first when nested).
    safe_links: list of {text, href} for safe-protocol links.
    unsafe_links: list of {text, href, reason} for unsafe-protocol links.

    A3 — link safety single-point:
      markdown-it-py refuses to parse unsafe-protocol links (javascript:/vbscript:/data:),
      leaving them as raw ``[label](href)`` text. This function detects such
      patterns in text tokens, strips them to ``label``, records them in
      ``unsafe_links``, and emits NO inline_mark (unsafe hrefs must not be
      exposed in marks). Safe links parsed by markdown-it-py (link_open) and
      safe links left as raw text (rare) both become inline_marks.
      html_inline is always stripped from text and flags has_inline_html
      (no rescue merge).
    """
    if not token.children:
        return token.content or "", [], [], [], False, False

    text_parts: list[str] = []
    marks: list[dict[str, Any]] = []
    safe_links: list[dict[str, str]] = []
    unsafe_links: list[dict[str, str]] = []
    # Stack of (mark_type, start_utf16_offset) for open marks.
    open_marks: list[tuple[str, int]] = []
    # Open link context: {start, href, is_safe, label_parts}.
    open_link: dict[str, Any] | None = None

    has_inline_html = False
    starts_with_html_inline = token.children[0].type == "html_inline"

    current_utf16 = 0

    def _append_text(s: str) -> None:
        nonlocal current_utf16
        text_parts.append(s)
        current_utf16 += utf16_code_unit_length(s)

    def _try_match_link_pattern(content: str, start_idx: int) -> tuple[str, str, int] | None:
        """Try to match ``[label](href)`` at content[start_idx].
        Returns (label, href, end_idx) or None.
        """
        if start_idx >= len(content) or content[start_idx] != "[":
            return None
        close_bracket = content.find("]", start_idx + 1)
        if close_bracket == -1:
            return None
        if close_bracket + 1 >= len(content) or content[close_bracket + 1] != "(":
            return None
        k = close_bracket + 2
        depth = 1
        length = len(content)
        while k < length and depth > 0:
            if content[k] == "(":
                depth += 1
            elif content[k] == ")":
                depth -= 1
            if depth > 0:
                k += 1
        if depth != 0:
            return None
        label = content[start_idx + 1:close_bracket]
        href = content[close_bracket + 2:k]
        return label, href, k + 1

    for child in token.children:
        ctype = child.type
        if ctype == "text":
            # Scan for unparsed [label](href) patterns (unsafe links that
            # markdown-it-py refused to parse, plus rare safe ones).
            content = child.content
            i = 0
            length = len(content)
            while i < length:
                if content[i] == "[":
                    match = _try_match_link_pattern(content, i)
                    if match is not None:
                        label, href, next_i = match
                        is_safe = _is_safe_link(href)
                        start = current_utf16
                        _append_text(label)
                        end = current_utf16
                        if is_safe:
                            marks.append(
                                {
                                    "type": "link",
                                    "start": start,
                                    "end": end,
                                    "href": href,
                                }
                            )
                            safe_links.append({"text": label, "href": href})
                        else:
                            unsafe_links.append(
                                {
                                    "text": label,
                                    "href": href,
                                    "reason": "unsafe_protocol",
                                }
                            )
                        if open_link is not None:
                            open_link["label_parts"].append(label)
                        i = next_i
                        continue
                # Batch regular characters up to next '[' to limit appends.
                next_bracket = content.find("[", i + 1)
                if next_bracket == -1:
                    next_bracket = length
                batch = content[i:next_bracket]
                _append_text(batch)
                if open_link is not None:
                    open_link["label_parts"].append(batch)
                i = next_bracket
        elif ctype == "code_inline":
            start = current_utf16
            _append_text(child.content)
            marks.append(
                {"type": "inline_code", "start": start, "end": current_utf16}
            )
            if open_link is not None:
                open_link["label_parts"].append(child.content)
        elif ctype in ("softbreak", "hardbreak"):
            _append_text("\n")
            if open_link is not None:
                open_link["label_parts"].append("\n")
        elif ctype == "link_open":
            href = ""
            if child.attrs and "href" in child.attrs:
                href = str(child.attrs["href"])
            open_link = {
                "start": current_utf16,
                "href": href,
                "is_safe": _is_safe_link(href),
                "label_parts": [],
            }
        elif ctype == "link_close":
            if open_link is not None:
                start = open_link["start"]
                end = current_utf16
                href = open_link["href"]
                label = "".join(open_link["label_parts"])
                if open_link["is_safe"]:
                    marks.append(
                        {
                            "type": "link",
                            "start": start,
                            "end": end,
                            "href": href,
                        }
                    )
                    safe_links.append({"text": label, "href": href})
                else:
                    unsafe_links.append(
                        {
                            "text": label,
                            "href": href,
                            "reason": "unsafe_protocol",
                        }
                    )
                open_link = None
        elif ctype in ("em_open", "strong_open", "s_open", "del_open"):
            mark_type = {
                "em_open": "em",
                "strong_open": "strong",
                "s_open": "strikethrough",
                "del_open": "strikethrough",
            }[ctype]
            open_marks.append((mark_type, current_utf16))
        elif ctype in ("em_close", "strong_close", "s_close", "del_close"):
            close_to_type = {
                "em_close": "em",
                "strong_close": "strong",
                "s_close": "strikethrough",
                "del_close": "strikethrough",
            }[ctype]
            # Pop the most recent matching open (handles nesting).
            for idx in range(len(open_marks) - 1, -1, -1):
                if open_marks[idx][0] == close_to_type:
                    mark_type, start = open_marks.pop(idx)
                    marks.append(
                        {
                            "type": mark_type,
                            "start": start,
                            "end": current_utf16,
                        }
                    )
                    break
        elif ctype == "html_inline":
            if _is_non_html_placeholder(child.content):
                # vector<T> / <name> style placeholders are plain text,
                # not HTML: preserve verbatim, no diagnostic.
                _append_text(child.content)
                if open_link is not None:
                    open_link["label_parts"].append(child.content)
            else:
                has_inline_html = True
                # Skip from text (A3: no rescue merge).
                continue
        elif ctype == "image":
            _append_text(child.content)
            if open_link is not None:
                open_link["label_parts"].append(child.content)
        elif ctype == "footnote_ref":
            continue
        else:
            if child.content:
                _append_text(child.content)
                if open_link is not None:
                    open_link["label_parts"].append(child.content)

    text = "".join(text_parts)
    return text, marks, safe_links, unsafe_links, has_inline_html, starts_with_html_inline


def _has_inline_html(token: Token) -> bool:
    """Check if an inline token contains html_inline children."""
    if not token.children:
        return False
    return any(child.type == "html_inline" for child in token.children)


def _has_footnote_ref(token: Token) -> bool:
    """Check if an inline token contains footnote_ref children."""
    if not token.children:
        return False
    return any(child.type == "footnote_ref" for child in token.children)


def _strip_html_tags(content: str) -> str:
    """Naive HTML tag stripping for raw html_block text extraction."""
    return re.sub(r"<[^>]+>", "", content).strip()


# Notion / clipboard <aside> containers: detect on raw HTML (before strip)
# so the semantic classifier can map to source_callout. Ordinary <div>
# must not match.
_HTML_ASIDE_OPEN_RE = re.compile(r"<\s*aside\b", re.IGNORECASE)
_HTML_ASIDE_CLOSE_RE = re.compile(r"<\s*/\s*aside\s*>", re.IGNORECASE)
# Stable payload key consumed only by semantic_classifier (single role seam).
SOURCE_SEMANTIC_HINT_HTML_ASIDE = "html_aside"


def _html_raw_is_aside(raw_html_chunks: list[str]) -> bool:
    joined = "".join(raw_html_chunks)
    return bool(
        _HTML_ASIDE_OPEN_RE.search(joined) and _HTML_ASIDE_CLOSE_RE.search(joined)
    )


# Known HTML tag names (WHATWG standard elements). A bare inline tag whose
# name is NOT in this set (``<T>``, ``<name>``) is treated as plain-text
# placeholder content (``vector<T>``, template arguments), not as HTML —
# it is preserved verbatim and never triggers an inline_html diagnostic.
_KNOWN_HTML_TAGS = frozenset(
    {
        "a", "abbr", "address", "area", "article", "aside", "audio",
        "b", "base", "bdi", "bdo", "blockquote", "body", "br", "button",
        "canvas", "caption", "cite", "code", "col", "colgroup",
        "data", "datalist", "dd", "del", "details", "dfn", "dialog", "div",
        "dl", "dt", "em", "embed", "fieldset", "figcaption", "figure",
        "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "head",
        "header", "hgroup", "hr", "html", "i", "iframe", "img", "input",
        "ins", "kbd", "label", "legend", "li", "link", "main", "map",
        "mark", "menu", "meta", "meter", "nav", "noscript", "object", "ol",
        "optgroup", "option", "output", "p", "picture", "pre", "progress",
        "q", "rp", "rt", "ruby", "s", "samp", "script", "search", "section",
        "select", "slot", "small", "source", "span", "strong", "style",
        "sub", "summary", "sup", "svg", "table", "tbody", "td", "template",
        "textarea", "tfoot", "th", "thead", "time", "title", "tr", "track",
        "u", "ul", "var", "video", "wbr",
    }
)

_HTML_INLINE_TAG_PATTERN = re.compile(
    r"^</?([A-Za-z][A-Za-z0-9-]*)((?:\s[^<>]*)?)\s*/?>$"
)


def _is_non_html_placeholder(content: str) -> bool:
    """Return True when an ``html_inline`` token is actually plain text.

    markdown-it-py parses any syntactically valid tag as ``html_inline``,
    including placeholders like ``<T>`` in ``vector<T>`` or ``<name>`` in
    prose. A *bare* tag (no attributes, not self-closing) whose name is
    not a known HTML element is preserved as literal text. Known tags,
    tags with attributes (``<img onerror=...>``), comments and doctype
    declarations stay on the strip-and-flag path (fail-safe).
    """
    match = _HTML_INLINE_TAG_PATTERN.match(content.strip())
    if match is None:
        return False
    if match.group(2):
        # Attributes present — real HTML, strip it.
        return False
    return match.group(1).lower() not in _KNOWN_HTML_TAGS


def _count_raw_table_cells(lines: list[str], line_1based: int) -> int | None:
    """Count raw ``|``-separated cells on a 1-based source line.

    Returns ``None`` when the line cannot be interpreted as a table row
    (out of range / no pipe). Unescaped pipes split cells; ``\\|`` is an
    escaped literal pipe and does not split.
    """
    if line_1based < 1 or line_1based > len(lines):
        return None
    line = lines[line_1based - 1].strip()
    if "|" not in line:
        return None
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return len(re.split(r"(?<!\\)\|", line))


def _audit_table_structure(blocks: list[ParsedBlock], source: str) -> bool:
    """Detect tables whose raw row/column structure markdown-it normalized.

    markdown-it silently pads missing cells and drops extra cells in body
    rows. A table is deterministic (safe to freeze as stable) iff it has
    exactly one header row and every row's raw cell count equals the
    header column count. Anything else is structure-uncertain: the table
    payload is stamped with ``structure_uncertain: True`` and the document
    routes to content check.

    Every table payload also gains ``header_rows`` (0 or 1 for GFM), so
    downstream consumers never re-derive header semantics from raw text.
    """
    lines = source.split("\n")
    children_by_parent: dict[str, list[ParsedBlock]] = {}
    for block in blocks:
        if block.parent_block_id:
            children_by_parent.setdefault(block.parent_block_id, []).append(block)

    any_uncertain = False
    for table in blocks:
        if table.block_type != "table":
            continue
        rows = [
            b
            for b in children_by_parent.get(table.block_id, [])
            if b.block_type == "table_row"
        ]
        header_rows = sum(
            1 for row in rows if row.payload_json.get("is_header") is True
        )
        table.payload_json["header_rows"] = header_rows
        column_count = int(table.payload_json.get("column_count") or 0)
        uncertain = header_rows != 1
        for row in rows:
            cells = [
                b
                for b in children_by_parent.get(row.block_id, [])
                if b.block_type == "table_cell"
            ]
            if len(cells) != column_count:
                uncertain = True
                continue
            raw_count = _count_raw_table_cells(
                lines, row.source_range.line_start
            )
            if raw_count is not None and raw_count != column_count:
                uncertain = True
        if uncertain:
            table.payload_json["structure_uncertain"] = True
            any_uncertain = True
    return any_uncertain


def _extract_alignment(token: Token) -> str:
    """Extract table cell alignment from token attrs.

    markdown-it-py stores alignment in attrs['style']='text-align:left'.
    None attrs means default alignment.
    """
    if not token.attrs:
        return "default"
    style = token.attrs.get("style")
    if not style:
        return "default"
    match = re.search(r"text-align:\s*(left|center|right)", style)
    if match:
        return match.group(1)
    return "default"


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


_LIST_OPEN_TYPES = frozenset({"bullet_list_open", "ordered_list_open"})
_NARRATIVE_BLOCK_TYPES = frozenset(
    {"paragraph", "heading", "list_item", "blockquote"}
)
_SKIP_TOKEN_TYPES = frozenset(
    {
        "thead_open", "thead_close",
        "tbody_open", "tbody_close",
        "inline",
        "paragraph_close", "heading_close",
        "footnote_anchor",
    }
)


class MarkdownSourceParser:
    """Parser adapter: Markdown text → MarkdownParseResult."""

    def __init__(self) -> None:
        self._md = (
            MarkdownIt(
                "commonmark",
                {"html": True, "linkify": False, "typographer": False},
            )
            .enable("table")
            .enable("strikethrough")
            .use(footnote_plugin)
        )

    def parse(self, text: str) -> MarkdownParseResult:
        """Parse Markdown text into a structured result."""
        normalized = _normalize_newlines(text)
        tokens = self._md.parse(normalized)

        blocks: list[ParsedBlock] = []
        warnings: list[DiagnosticWarning] = []
        unsupported: list[UnsupportedFeature] = []
        flags = _DiagnosticFlags()

        parent_stack: list[str] = []
        # Stack of (block_id, block_type, line_end) for parent context
        parent_context: list[tuple[str, str, int]] = []
        # Stack of list contexts: {"ordered": bool, "next_ordinal": int}
        list_context_stack: list[dict[str, Any]] = []
        # Stack of current tr_open map for cell source_range derivation
        current_tr_map: list[int] | None = None
        # Table context
        table_alignments: list[str] = []
        table_column_index = 0
        table_row_index = 0

        order_index = 0
        footnote_counter = 1
        i = 0

        # Pre-scan for strikethrough detection (token-level, not string).
        # s_open/del_open may appear as inline children inside `inline` tokens,
        # not as top-level tokens — check both levels.
        has_strikethrough_token = any(
            t.type in ("s_open", "del_open")
            or (
                t.type == "inline"
                and t.children is not None
                and any(c.type in ("s_open", "del_open") for c in t.children)
            )
            for t in tokens
        )
        if has_strikethrough_token:
            flags.has_strikethrough = True

        # Pre-scan: unclosed fence detection (odd count of ``` in source).
        # Must run before the token loop so fence blocks can use the flag.
        fence_count = normalized.count("```")
        if fence_count % 2 != 0:
            flags.has_unclosed_fence = True
        # Count total fence tokens to identify the last one (for closed=False).
        total_fence_tokens = sum(1 for t in tokens if t.type == "fence")
        fence_tokens_seen = 0

        while i < len(tokens):
            token = tokens[i]
            token_type = token.type

            # --- Skip tokens we don't produce blocks for ---
            if token_type in _SKIP_TOKEN_TYPES:
                i += 1
                continue

            if token_type in (
                "bullet_list_close", "ordered_list_close",
                "list_item_close", "blockquote_close",
                "table_close", "tr_close",
            ):
                if parent_stack:
                    parent_stack.pop()
                if parent_context:
                    parent_context.pop()
                if token_type in ("bullet_list_close", "ordered_list_close"):
                    if list_context_stack:
                        list_context_stack.pop()
                i += 1
                continue

            # --- Raw HTML block aggregation (M-6) ---
            if token_type == "html_block":
                flags.has_raw_html = True
                agg_start_map = token.map
                agg_texts: list[str] = []
                raw_html_chunks: list[str] = []
                agg_end_map = token.map
                j = i
                while j < len(tokens):
                    t = tokens[j]
                    if t.type == "html_block":
                        raw_html_chunks.append(t.content or "")
                        stripped = _strip_html_tags(t.content)
                        if stripped:
                            agg_texts.append(stripped)
                        if t.map:
                            agg_end_map = t.map
                        is_closing = t.content.strip().startswith("</")
                        # Self-contained <aside>...</aside> must not absorb the
                        # following prose paragraph into the same block.
                        is_complete_aside = _html_raw_is_aside([t.content or ""])
                        j += 1
                        if is_closing or is_complete_aside:
                            break
                        continue
                    elif t.type == "paragraph_open":
                        k = j + 1
                        while k < len(tokens) and tokens[k].type != "paragraph_close":
                            if tokens[k].type == "inline":
                                agg_texts.append(_extract_inline_text(tokens[k]))
                                # L1: inline HTML inside a paragraph absorbed
                                # by the html_block aggregation must still be
                                # diagnosed (it is stripped, not executed).
                                if _has_inline_html(tokens[k]):
                                    flags.has_inline_html = True
                            k += 1
                        if t.map:
                            agg_end_map = t.map
                        j = k + 1
                        # After paragraph, check if next is html_block
                        if j >= len(tokens) or tokens[j].type != "html_block":
                            break
                        continue
                    else:
                        break

                src_start = _map_to_1based(agg_start_map)
                src_end = _map_to_1based(agg_end_map)
                if src_start and src_end:
                    final_range = SourceRange(
                        line_start=src_start.line_start,
                        line_end=src_end.line_end,
                    )
                else:
                    final_range = _resolve_range(src_start, flags)

                # Notion/clipboard <aside>: preserve a stable semantic hint
                # for the single classifier seam. Prefer blockquote so the
                # structure is not treated as ordinary prose. Plain <div>
                # never sets the hint.
                is_aside = _html_raw_is_aside(raw_html_chunks)
                payload: dict[str, Any] = {"extracted_from": "html_block"}
                if is_aside:
                    payload["source_semantic_hint"] = SOURCE_SEMANTIC_HINT_HTML_ASIDE
                blocks.append(
                    ParsedBlock(
                        block_id=f"b{order_index + 1}",
                        block_type="blockquote" if is_aside else "paragraph",
                        text_content=" ".join(t for t in agg_texts if t).strip(),
                        payload_json=payload,
                        parent_block_id=parent_stack[-1] if parent_stack else None,
                        order_index=order_index,
                        source_range=final_range,
                    )
                )
                order_index += 1
                i = j
                continue

            # --- List wrapper (M-5) ---
            if token_type in _LIST_OPEN_TYPES:
                ordered = token_type == "ordered_list_open"
                start = 1
                if token.attrs and "start" in token.attrs:
                    try:
                        start = int(token.attrs["start"])
                    except (ValueError, TypeError):
                        start = 1
                depth = len(list_context_stack)
                src_range = _map_to_1based(token.map)
                # Extend line_end to cover parent list_item range (M-5 fix)
                if src_range and parent_context and parent_context[-1][1] == "list_item":
                    parent_line_end = parent_context[-1][2]
                    if parent_line_end > src_range.line_end:
                        src_range = SourceRange(
                            line_start=src_range.line_start,
                            line_end=parent_line_end,
                        )
                list_id = f"b{order_index + 1}"
                blocks.append(
                    ParsedBlock(
                        block_id=list_id,
                        block_type="list",
                        text_content=None,
                        payload_json={"ordered": ordered, "depth": depth},
                        parent_block_id=parent_stack[-1] if parent_stack else None,
                        order_index=order_index,
                        source_range=_resolve_range(src_range, flags),
                    )
                )
                order_index += 1
                parent_stack.append(list_id)
                parent_context.append(
                    (list_id, "list", src_range.line_end if src_range else 0)
                )
                list_context_stack.append(
                    {"ordered": ordered, "next_ordinal": start}
                )
                i += 1
                continue

            # --- Blockquote wrapper ---
            if token_type == "blockquote_open":
                src_range = _map_to_1based(token.map)
                bq_id = f"b{order_index + 1}"
                bq_text = ""
                bq_marks: list[dict[str, Any]] = []
                # Consume blockquote content
                j = i + 1
                while j < len(tokens) and tokens[j].type != "blockquote_close":
                    if tokens[j].type == "inline":
                        (
                            bq_text,
                            bq_marks,
                            _bq_safe_links,
                            _bq_unsafe_links,
                            _bq_has_html,
                            _bq_starts_html,
                        ) = _process_inline_with_marks(tokens[j])
                        if _bq_unsafe_links:
                            flags.has_unsafe_link = True
                    j += 1
                bq_payload: dict[str, Any] = {}
                if bq_marks:
                    bq_payload["inline_marks"] = bq_marks
                blocks.append(
                    ParsedBlock(
                        block_id=bq_id,
                        block_type="blockquote",
                        text_content=bq_text,
                        payload_json=bq_payload,
                        parent_block_id=parent_stack[-1] if parent_stack else None,
                        order_index=order_index,
                        source_range=_resolve_range(src_range, flags),
                    )
                )
                order_index += 1
                parent_stack.append(bq_id)
                parent_context.append(
                    (bq_id, "blockquote", src_range.line_end if src_range else 0)
                )
                i = j
                continue

            # --- Table wrapper (M-4) ---
            if token_type == "table_open":
                # Pre-scan alignments from thead th_open tokens
                table_alignments = []
                j = i + 1
                in_thead_scan = False
                while j < len(tokens) and tokens[j].type != "table_close":
                    if tokens[j].type == "thead_open":
                        in_thead_scan = True
                    elif tokens[j].type == "thead_close":
                        break
                    elif tokens[j].type == "th_open" and in_thead_scan:
                        table_alignments.append(_extract_alignment(tokens[j]))
                    j += 1
                column_count = len(table_alignments)
                src_range = _map_to_1based(token.map)
                tbl_id = f"b{order_index + 1}"
                blocks.append(
                    ParsedBlock(
                        block_id=tbl_id,
                        block_type="table",
                        text_content=None,
                        payload_json={
                            "alignments": table_alignments,
                            "column_count": column_count,
                        },
                        parent_block_id=parent_stack[-1] if parent_stack else None,
                        order_index=order_index,
                        source_range=_resolve_range(src_range, flags),
                    )
                )
                order_index += 1
                parent_stack.append(tbl_id)
                parent_context.append(
                    (tbl_id, "table", src_range.line_end if src_range else 0)
                )
                table_row_index = 0
                i += 1
                continue

            if token_type == "tr_open":
                src_range = _map_to_1based(token.map)
                current_tr_map = token.map
                # Determine if header row (inside thead)
                # Check if any th_open follows before tr_close
                is_header = False
                j = i + 1
                while j < len(tokens) and tokens[j].type != "tr_close":
                    if tokens[j].type == "th_open":
                        is_header = True
                        break
                    if tokens[j].type == "td_open":
                        is_header = False
                        break
                    j += 1
                tr_id = f"b{order_index + 1}"
                blocks.append(
                    ParsedBlock(
                        block_id=tr_id,
                        block_type="table_row",
                        text_content=None,
                        payload_json={
                            "is_header": is_header,
                            "row_index": table_row_index,
                        },
                        parent_block_id=parent_stack[-1] if parent_stack else None,
                        order_index=order_index,
                        source_range=_resolve_range(src_range, flags),
                    )
                )
                order_index += 1
                parent_stack.append(tr_id)
                parent_context.append(
                    (tr_id, "table_row", src_range.line_end if src_range else 0)
                )
                table_column_index = 0
                table_row_index += 1
                i += 1
                continue

            if token_type in ("td_open", "th_open"):
                # source_range from parent tr_open map (th_open/td_open map=None)
                src_range = _map_to_1based(current_tr_map)
                cell_text = ""
                cell_marks: list[dict[str, Any]] = []
                j = i + 1
                while (
                    j < len(tokens)
                    and tokens[j].type not in ("td_close", "th_close")
                ):
                    if tokens[j].type == "inline":
                        (
                            cell_text,
                            cell_marks,
                            _cell_safe_links,
                            _cell_unsafe_links,
                            _cell_has_html,
                            _cell_starts_html,
                        ) = _process_inline_with_marks(tokens[j])
                        if _cell_unsafe_links:
                            flags.has_unsafe_link = True
                    j += 1
                is_header = token_type == "th_open"
                alignment = _extract_alignment(token)
                cell_payload: dict[str, Any] = {
                    "column_index": table_column_index,
                    "alignment": alignment,
                    "is_header": is_header,
                }
                if cell_marks:
                    cell_payload["inline_marks"] = cell_marks
                blocks.append(
                    ParsedBlock(
                        block_id=f"b{order_index + 1}",
                        block_type="table_cell",
                        text_content=cell_text,
                        payload_json=cell_payload,
                        parent_block_id=parent_stack[-1] if parent_stack else None,
                        order_index=order_index,
                        source_range=_resolve_range(src_range, flags),
                    )
                )
                order_index += 1
                table_column_index += 1
                i = j + 1
                continue

            # --- Heading ---
            if token_type == "heading_open":
                level = int(token.tag[1:]) if token.tag.startswith("h") else 1
                src_range = _map_to_1based(token.map)
                heading_text = ""
                heading_marks: list[dict[str, Any]] = []
                j = i + 1
                while j < len(tokens) and tokens[j].type != "heading_close":
                    if tokens[j].type == "inline":
                        (
                            heading_text,
                            heading_marks,
                            _heading_safe_links,
                            _heading_unsafe_links,
                            _heading_has_html,
                            _heading_starts_html,
                        ) = _process_inline_with_marks(tokens[j])
                        if _heading_unsafe_links:
                            flags.has_unsafe_link = True
                    j += 1
                heading_payload: dict[str, Any] = {"level": level}
                if heading_marks:
                    heading_payload["inline_marks"] = heading_marks
                blocks.append(
                    ParsedBlock(
                        block_id=f"b{order_index + 1}",
                        block_type="heading",
                        text_content=heading_text,
                        payload_json=heading_payload,
                        parent_block_id=parent_stack[-1] if parent_stack else None,
                        order_index=order_index,
                        source_range=_resolve_range(src_range, flags),
                    )
                )
                order_index += 1
                i = j + 1
                continue

            # --- Paragraph (M-3, M-6) ---
            if token_type == "paragraph_open":
                src_range = _map_to_1based(token.map)
                inline_token: Token | None = None
                j = i + 1
                while j < len(tokens) and tokens[j].type != "paragraph_close":
                    if tokens[j].type == "inline":
                        inline_token = tokens[j]
                    j += 1

                payload: dict[str, Any] = {}
                para_text = ""
                if inline_token is not None:
                    (
                        para_text,
                        inline_marks,
                        safe_links,
                        unsafe_links,
                        has_inline_html,
                        starts_with_html_inline,
                    ) = _process_paragraph_inline(inline_token)
                    # html_inline tokens never contribute raw tag text to
                    # para_text (they are either stripped or, for non-HTML
                    # placeholders like vector<T>, preserved verbatim as
                    # intentional literal text), so no regex tag-stripping
                    # post-pass is applied here.
                    # A3: html_inline is always flagged when present (no
                    # "rescue" merge — link safety is single-point).
                    if has_inline_html:
                        flags.has_inline_html = True
                    if unsafe_links:
                        flags.has_unsafe_link = True
                    # Always include `links` key when any links exist (safe or
                    # unsafe). When only unsafe links exist, links=[].
                    if safe_links or unsafe_links:
                        payload["links"] = safe_links
                    if unsafe_links:
                        payload["stripped_links"] = unsafe_links
                    # A2: inline_marks only when non-empty (minimal payload).
                    if inline_marks:
                        payload["inline_marks"] = inline_marks
                    # M-6: paragraph starting with html_inline
                    if starts_with_html_inline:
                        payload["extracted_from"] = "html_inline"
                    # Footnote reference detection
                    if _has_footnote_ref(inline_token):
                        flags.has_footnote_ref = True

                blocks.append(
                    ParsedBlock(
                        block_id=f"b{order_index + 1}",
                        block_type="paragraph",
                        text_content=para_text,
                        payload_json=payload,
                        parent_block_id=parent_stack[-1] if parent_stack else None,
                        order_index=order_index,
                        source_range=_resolve_range(src_range, flags),
                    )
                )
                order_index += 1
                i = j + 1
                continue

            # --- Fence (code block) — M-1: rstrip trailing \n ---
            if token_type == "fence":
                fence_tokens_seen += 1
                src_range = _map_to_1based(token.map)
                language = token.info.strip() if token.info else ""
                # An unclosed fence is the last fence token when has_unclosed_fence
                # is True (odd count of ``` in source).
                is_closed = not (
                    flags.has_unclosed_fence
                    and fence_tokens_seen == total_fence_tokens
                )
                blocks.append(
                    ParsedBlock(
                        block_id=f"b{order_index + 1}",
                        block_type="code_block",
                        text_content=token.content.rstrip("\n"),
                        payload_json={
                            "language": language,
                            "fenced": True,
                            "closed": is_closed,
                        },
                        parent_block_id=parent_stack[-1] if parent_stack else None,
                        order_index=order_index,
                        source_range=_resolve_range(src_range, flags),
                    )
                )
                order_index += 1
                i += 1
                continue

            # --- Indented code block — M-1: rstrip trailing \n ---
            if token_type == "code_block":
                src_range = _map_to_1based(token.map)
                blocks.append(
                    ParsedBlock(
                        block_id=f"b{order_index + 1}",
                        block_type="code_block",
                        text_content=token.content.rstrip("\n"),
                        payload_json={
                            "language": "",
                            "fenced": False,
                            "closed": True,
                        },
                        parent_block_id=parent_stack[-1] if parent_stack else None,
                        order_index=order_index,
                        source_range=_resolve_range(src_range, flags),
                    )
                )
                order_index += 1
                i += 1
                continue

            # --- Thematic break (hr) ---
            if token_type == "hr":
                src_range = _map_to_1based(token.map)
                blocks.append(
                    ParsedBlock(
                        block_id=f"b{order_index + 1}",
                        block_type="thematic_break",
                        text_content=None,
                        payload_json={},
                        parent_block_id=parent_stack[-1] if parent_stack else None,
                        order_index=order_index,
                        source_range=_resolve_range(src_range, flags),
                    )
                )
                order_index += 1
                i += 1
                continue

            # --- List item (M-5) ---
            if token_type == "list_item_open":
                src_range = _map_to_1based(token.map)
                li_id = f"b{order_index + 1}"
                li_text = ""
                li_marks: list[dict[str, Any]] = []
                # Only consume the list item's own paragraph (for text).
                # Do NOT consume nested list tokens — let the main loop
                # process them so nested lists produce their own blocks.
                j = i + 1
                consumed_end = i + 1  # default: just advance past list_item_open
                while j < len(tokens):
                    t = tokens[j]
                    if t.type == "paragraph_open":
                        # Extract text from inline inside this paragraph
                        k = j + 1
                        while k < len(tokens) and tokens[k].type != "paragraph_close":
                            if tokens[k].type == "inline":
                                (
                                    li_text,
                                    li_marks,
                                    _li_safe_links,
                                    _li_unsafe_links,
                                    _li_has_html,
                                    _li_starts_html,
                                ) = _process_inline_with_marks(tokens[k])
                                if _li_unsafe_links:
                                    flags.has_unsafe_link = True
                            k += 1
                        consumed_end = k + 1  # advance past paragraph_close
                        break
                    elif t.type in _LIST_OPEN_TYPES:
                        # Nested list starts before any paragraph — no direct text
                        consumed_end = j  # position at nested list_open
                        break
                    elif (
                        t.type == "list_item_close"
                        and t.level == token.level
                    ):
                        # Empty list item (no content)
                        consumed_end = j  # position at list_item_close
                        break
                    elif t.type == "inline":
                        # Bare inline (no paragraph wrapper)
                        (
                            li_text,
                            li_marks,
                            _li_safe_links,
                            _li_unsafe_links,
                            _li_has_html,
                            _li_starts_html,
                        ) = _process_inline_with_marks(t)
                        if _li_unsafe_links:
                            flags.has_unsafe_link = True
                        consumed_end = j + 1
                        break
                    j += 1

                # Get list context for ordered/ordinal/depth
                if list_context_stack:
                    ctx = list_context_stack[-1]
                    ordered = ctx["ordered"]
                    if ordered:
                        ordinal: int | None = ctx["next_ordinal"]
                        ctx["next_ordinal"] += 1
                    else:
                        ordinal = None
                    depth = len(list_context_stack) - 1
                else:
                    ordered = False
                    ordinal = None
                    depth = 0

                # Construct marker
                if ordered and ordinal is not None:
                    marker = f"{ordinal}{token.markup}"
                else:
                    marker = token.markup

                li_payload: dict[str, Any] = {
                    "ordered": ordered,
                    "marker": marker,
                    "ordinal": ordinal,
                    "depth": depth,
                }
                if li_marks:
                    li_payload["inline_marks"] = li_marks
                blocks.append(
                    ParsedBlock(
                        block_id=li_id,
                        block_type="list_item",
                        text_content=li_text,
                        payload_json=li_payload,
                        parent_block_id=parent_stack[-1] if parent_stack else None,
                        order_index=order_index,
                        source_range=_resolve_range(src_range, flags),
                    )
                )
                order_index += 1
                parent_stack.append(li_id)
                parent_context.append(
                    (
                        li_id,
                        "list_item",
                        src_range.line_end if src_range else 0,
                    )
                )
                i = consumed_end
                continue

            # --- Footnote block (M-2) ---
            if token_type == "footnote_block_open":
                # Find paragraph inside footnote for source_range and text
                fn_src_range: SourceRange | None = None
                fn_text = ""
                j = i + 1
                while (
                    j < len(tokens)
                    and tokens[j].type != "footnote_block_close"
                ):
                    if tokens[j].type == "paragraph_open":
                        if fn_src_range is None:
                            fn_src_range = _map_to_1based(tokens[j].map)
                        k = j + 1
                        while k < len(tokens) and tokens[k].type != "paragraph_close":
                            if tokens[k].type == "inline":
                                fn_text = _extract_inline_text(tokens[k])
                            k += 1
                        j = k + 1
                        continue
                    j += 1
                footnote_id = str(footnote_counter)
                footnote_counter += 1
                blocks.append(
                    ParsedBlock(
                        block_id=f"b{order_index + 1}",
                        block_type="footnote",
                        text_content=fn_text,
                        payload_json={"footnote_id": footnote_id},
                        parent_block_id=None,
                        order_index=order_index,
                        source_range=_resolve_range(fn_src_range, flags),
                    )
                )
                order_index += 1
                i = j + 1
                continue

            # --- Skip unknown tokens ---
            i += 1

        # --- Diagnostics ---
        # Unclosed fence detection moved to pre-scan (before token loop)
        # so fence blocks can reference flags.has_unclosed_fence.

        # L1: audit table structure (header rows / raw cell counts) and
        # stamp header_rows + structure_uncertain onto table payloads.
        if _audit_table_structure(blocks, normalized):
            flags.has_table_structure_uncertain = True

        if flags.has_raw_html:
            warnings.append(
                DiagnosticWarning(
                    code="raw_html_block",
                    message=_MSG_RAW_HTML_BLOCK,
                    blocks_freeze=False,
                    classification=CLASSIFICATION_ADAPTATION_NOTICE,
                )
            )
            unsupported.append(
                UnsupportedFeature(
                    code="raw_html",
                    message=_MSG_UNSUP_RAW_HTML,
                )
            )

        if flags.has_inline_html:
            warnings.append(
                DiagnosticWarning(
                    code="inline_html",
                    message=_MSG_INLINE_HTML,
                    blocks_freeze=False,
                    classification=CLASSIFICATION_ADAPTATION_NOTICE,
                )
            )

        if flags.has_unsafe_link:
            warnings.append(
                DiagnosticWarning(
                    code="unsafe_link_protocol",
                    message=_MSG_UNSAFE_LINK,
                    blocks_freeze=False,
                    classification=CLASSIFICATION_ADAPTATION_NOTICE,
                )
            )
            unsupported.append(
                UnsupportedFeature(
                    code="unsafe_link_sanitization",
                    message=_MSG_UNSUP_UNSAFE_LINK,
                )
            )

        if flags.has_footnote_ref:
            warnings.append(
                DiagnosticWarning(
                    code="footnote_reference",
                    message=_MSG_FOOTNOTE_REF,
                    blocks_freeze=False,
                    classification=CLASSIFICATION_CONTENT_CHECK,
                )
            )
            unsupported.append(
                UnsupportedFeature(
                    code="footnote_full_semantics",
                    message=_MSG_UNSUP_FOOTNOTE,
                )
            )

        if flags.has_unclosed_fence:
            warnings.append(
                DiagnosticWarning(
                    code="has_unclosed_fence",
                    message=_MSG_UNCLOSED_FENCE,
                    blocks_freeze=False,
                    classification=CLASSIFICATION_CONTENT_CHECK,
                )
            )

        if flags.has_table_structure_uncertain:
            warnings.append(
                DiagnosticWarning(
                    code="table_structure_uncertain",
                    message=_MSG_TABLE_STRUCTURE_UNCERTAIN,
                    blocks_freeze=False,
                    classification=CLASSIFICATION_CONTENT_CHECK,
                )
            )

        if flags.has_missing_source_range:
            warnings.append(
                DiagnosticWarning(
                    code="missing_source_range",
                    message=_MSG_MISSING_SOURCE_RANGE,
                    blocks_freeze=False,
                    classification=CLASSIFICATION_CONTENT_CHECK,
                )
            )

        if flags.has_strikethrough:
            warnings.append(
                DiagnosticWarning(
                    code="strikethrough_extension",
                    message=_MSG_STRIKETHROUGH,
                    blocks_freeze=False,
                    classification=CLASSIFICATION_SILENT,
                )
            )

        # Mermaid detection
        for b in blocks:
            if b.block_type == "code_block":
                lang = b.payload_json.get("language", "")
                if lang == "mermaid":
                    warnings.append(
                        DiagnosticWarning(
                            code="mermaid_static_only",
                            message=_MSG_MERMAID,
                            blocks_freeze=False,
                            classification=CLASSIFICATION_ADAPTATION_NOTICE,
                        )
                    )
                    break

        # --- Outcome determination (L1 classification-driven) ---
        has_narrative = any(
            b.block_type in _NARRATIVE_BLOCK_TYPES for b in blocks
        )
        has_code = any(b.block_type == "code_block" for b in blocks)
        has_content_check = any(
            warning.classification == CLASSIFICATION_CONTENT_CHECK
            for warning in warnings
        )

        if not has_narrative and has_code:
            warnings.append(
                DiagnosticWarning(
                    code="code_dominant",
                    message=_MSG_CODE_DOMINANT,
                    blocks_freeze=False,
                    classification=CLASSIFICATION_CONTENT_CHECK,
                )
            )
            outcome = "input_rejected_or_action_required"
        elif has_content_check:
            outcome = "candidate_document_required"
        else:
            outcome = "stable_document_ready"

        return MarkdownParseResult(
            parser_name=PARSER_NAME,
            parser_version=PARSER_VERSION,
            profile=PROFILE,
            blocks=tuple(blocks),
            warnings=tuple(warnings),
            unsupported=tuple(unsupported),
            outcome=outcome,
        )


# ---------------------------------------------------------------------------
# Internal: diagnostic flags accumulator
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _DiagnosticFlags:
    has_raw_html: bool = False
    has_inline_html: bool = False
    has_unsafe_link: bool = False
    has_footnote_ref: bool = False
    has_unclosed_fence: bool = False
    has_strikethrough: bool = False
    has_missing_source_range: bool = False
    has_table_structure_uncertain: bool = False
