"""TDD tests for A2 (inline marks in payload_json) and A3 (link safety
single-point convergence).

Contract (D4 / A2):
    payload_json.inline_marks = [
        {"type": "strong|em|strikethrough|inline_code|link",
         "start": <utf16_offset>, "end": <utf16_offset>,
         "href": "<safe url>"  # link only
        }
    ]

Offsets are UTF-16 code unit offsets within the block ``text_content``
(consistent with the base/anchor system using
``app.contracts.annotation.utf16_code_unit_length``).

A3 rules:
  * html_inline + link overlap → fail-closed to candidate_document_required
    with ``inline_html`` warning (no "rescue" merge via
    ``_reconstruct_raw_with_html``).
  * Safe-protocol whitelist (http/https/mailto); other protocols stripped
    with ``unsafe_link_protocol`` warning.
  * Merge semantics for non-html_inline unsafe links (javascript: /
    vbscript:) preserved and exercised by exhaustive fixtures.
"""

from __future__ import annotations

from app.contracts.annotation import utf16_code_unit_length
from app.services.reader_orchestration.markdown_source_parser import (
    MarkdownSourceParser,
    ParsedBlock,
)


def _parse(text: str) -> list[ParsedBlock]:
    return list(MarkdownSourceParser().parse(text).blocks)


def _first_block(text: str) -> ParsedBlock:
    blocks = _parse(text)
    assert blocks, f"expected at least 1 block, got 0 for {text!r}"
    return blocks[0]


def _utf16_len(s: str) -> int:
    return utf16_code_unit_length(s)


# ---------------------------------------------------------------------------
# A2: inline_marks basics
# ---------------------------------------------------------------------------


def test_a2_strong_mark_in_paragraph_payload() -> None:
    """**bold** → inline_marks=[{type:strong, start:0, end:4}]."""
    block = _first_block("**bold** rest")
    marks = block.payload_json.get("inline_marks")
    assert marks == [{"type": "strong", "start": 0, "end": 4}], (
        f"expected single strong mark [0,4], got {marks!r}"
    )
    # text_content is the flattened plain text (no ** markers)
    assert block.text_content == "bold rest"
    # offsets are UTF-16 code units within text_content
    assert _utf16_len("bold") == 4


def test_a2_em_mark_in_paragraph_payload() -> None:
    """*em* → inline_marks=[{type:em, start:0, end:2}]."""
    block = _first_block("*em* tail")
    marks = block.payload_json.get("inline_marks")
    assert marks == [{"type": "em", "start": 0, "end": 2}], (
        f"expected single em mark [0,2], got {marks!r}"
    )
    assert block.text_content == "em tail"


def test_a2_strikethrough_mark_in_paragraph_payload() -> None:
    """~~strike~~ → inline_marks=[{type:strikethrough, start:0, end:6}]."""
    block = _first_block("~~strike~~ tail")
    marks = block.payload_json.get("inline_marks")
    assert marks == [{"type": "strikethrough", "start": 0, "end": 6}], (
        f"expected single strikethrough mark [0,6], got {marks!r}"
    )
    assert block.text_content == "strike tail"


def test_a2_inline_code_mark_in_paragraph_payload() -> None:
    """`code` → inline_marks=[{type:inline_code, start:0, end:4}]."""
    block = _first_block("`code` tail")
    marks = block.payload_json.get("inline_marks")
    assert marks == [{"type": "inline_code", "start": 0, "end": 4}], (
        f"expected single inline_code mark [0,4], got {marks!r}"
    )
    assert block.text_content == "code tail"


def test_a2_safe_link_mark_in_paragraph_payload() -> None:
    """[label](https://example.com) → link mark with href."""
    block = _first_block("[label](https://example.com) tail")
    marks = block.payload_json.get("inline_marks")
    assert marks == [
        {"type": "link", "start": 0, "end": 5, "href": "https://example.com"}
    ], f"expected link mark [0,5] with href, got {marks!r}"
    assert block.text_content == "label tail"
    # Safe links still recorded in payload_json.links (back-compat)
    assert block.payload_json.get("links") == [
        {"text": "label", "href": "https://example.com"}
    ]


def test_a2_nested_marks_strong_em_overlay() -> None:
    """**a *b* c** → strong [0,5] + em [2,3] (overlay, not tree)."""
    block = _first_block("**a *b* c**")
    marks = block.payload_json.get("inline_marks")
    # text_content = "a b c" → strong covers [0,5], em covers [2,3]
    assert block.text_content == "a b c"
    assert {"type": "strong", "start": 0, "end": 5} in marks, (
        f"strong [0,5] missing from {marks!r}"
    )
    assert {"type": "em", "start": 2, "end": 3} in marks, (
        f"em [2,3] missing from {marks!r}"
    )


def test_a2_inline_code_inside_strong_overlay() -> None:
    """**strong `code` end** → strong [0,10] + inline_code [7,11]."""
    block = _first_block("**strong `code` end**")
    # text_content = "strong code end" → len=15
    assert block.text_content == "strong code end"
    marks = block.payload_json.get("inline_marks")
    # strong covers whole range [0, 15]
    assert {"type": "strong", "start": 0, "end": 15} in marks, (
        f"strong [0,15] missing from {marks!r}"
    )
    # inline_code covers "code" at offset 7..11
    assert {"type": "inline_code", "start": 7, "end": 11} in marks, (
        f"inline_code [7,11] missing from {marks!r}"
    )


def test_a2_utf16_offsets_with_emoji() -> None:
    """Offsets must be UTF-16 code units (emoji = 2 UTF-16 units)."""
    # "x 😀 " is x(1) + space(1) + emoji(2) + space(1) = 5 UTF-16 units
    # then "bold" at [5,9]
    block = _first_block("x 😀 **bold**")
    assert block.text_content == "x 😀 bold"
    marks = block.payload_json.get("inline_marks")
    expected_start = _utf16_len("x 😀 ")
    assert {"type": "strong", "start": expected_start, "end": expected_start + 4} in marks, (
        f"strong [{expected_start},{expected_start + 4}] missing from {marks!r}; "
        f"text_content={block.text_content!r}"
    )


def test_a2_heading_carries_inline_marks() -> None:
    """heading block payload_json.inline_marks (in addition to level)."""
    block = _first_block("# Head **bold**")
    assert block.block_type == "heading"
    assert block.payload_json.get("level") == 1
    marks = block.payload_json.get("inline_marks")
    # text_content = "Head bold" → strong [5,9]
    assert {"type": "strong", "start": 5, "end": 9} in marks, (
        f"strong [5,9] missing from {marks!r}"
    )


def test_a2_list_item_carries_inline_marks() -> None:
    """list_item payload_json.inline_marks."""
    blocks = _parse("- item with **bold**")
    list_item = next(b for b in blocks if b.block_type == "list_item")
    marks = list_item.payload_json.get("inline_marks")
    # text_content = "item with bold" → strong [10,14]
    assert list_item.text_content == "item with bold"
    assert {"type": "strong", "start": 10, "end": 14} in marks, (
        f"strong [10,14] missing from {marks!r}"
    )


def test_a2_blockquote_carries_inline_marks() -> None:
    """blockquote payload_json.inline_marks."""
    block = _first_block("> quote with **bold**")
    assert block.block_type == "blockquote"
    marks = block.payload_json.get("inline_marks")
    # text_content = "quote with bold" → strong [11,15]
    assert {"type": "strong", "start": 11, "end": 15} in marks, (
        f"strong [11,15] missing from {marks!r}"
    )


def test_a2_table_cell_carries_inline_marks() -> None:
    """table_cell payload_json.inline_marks."""
    blocks = _parse("| H1 |\n|----|\n| **b** |")
    cell = next(
        b
        for b in blocks
        if b.block_type == "table_cell" and not b.payload_json.get("is_header")
    )
    marks = cell.payload_json.get("inline_marks")
    # text_content = "b" → strong [0,1]
    assert {"type": "strong", "start": 0, "end": 1} in marks, (
        f"strong [0,1] missing from {marks!r}"
    )


def test_a2_code_block_has_no_inline_marks() -> None:
    """code_block does not carry inline_marks (whole block is code)."""
    block = _first_block("```\ncode with **stars**\n```")
    assert block.block_type == "code_block"
    assert "inline_marks" not in block.payload_json, (
        f"code_block must not carry inline_marks, got {block.payload_json!r}"
    )


def test_a2_paragraph_no_inline_marks_when_plain_text() -> None:
    """Plain paragraph (no marks) → payload_json has no inline_marks key
    OR inline_marks=[]. Either is acceptable; we assert the key is absent
    to keep payloads minimal.
    """
    block = _first_block("just plain text")
    assert "inline_marks" not in block.payload_json, (
        f"plain paragraph should not carry inline_marks, got {block.payload_json!r}"
    )


def test_a2_link_with_title_mark_uses_href_only() -> None:
    """[t](https://x.com "title") → link mark with href (title ignored)."""
    block = _first_block('[t](https://x.com "some title") tail')
    marks = block.payload_json.get("inline_marks")
    assert marks == [
        {"type": "link", "start": 0, "end": 1, "href": "https://x.com"}
    ], f"expected link mark with href only (no title), got {marks!r}"
    assert block.text_content == "t tail"


def test_a2_inline_marks_offsets_consistent_with_text_content() -> None:
    """All inline_marks start/end must be valid UTF-16 offsets within
    text_content (0 <= start <= end <= utf16_len(text_content)).
    """
    block = _first_block("**a** *b* ~~c~~ `d` [e](https://x.com)")
    text = block.text_content or ""
    text_utf16_len = _utf16_len(text)
    marks = block.payload_json.get("inline_marks", [])
    for m in marks:
        assert 0 <= m["start"] <= m["end"] <= text_utf16_len, (
            f"mark {m!r} out of bounds for text={text!r} (utf16_len={text_utf16_len})"
        )


# ---------------------------------------------------------------------------
# A3: link safety single-point convergence
# ---------------------------------------------------------------------------


def test_a3_javascript_unsafe_link_stripped_with_warning() -> None:
    """[x](javascript:alert(1)) → stripped, unsafe_link_protocol warning,
    candidate outcome. Merge semantics preserved (no html_inline).
    """
    result = MarkdownSourceParser().parse("[x](javascript:alert(1)) tail")
    warning_codes = {w.code for w in result.warnings}
    assert "unsafe_link_protocol" in warning_codes, (
        f"unsafe_link_protocol warning missing: {warning_codes!r}"
    )
    assert result.outcome == "candidate_document_required"
    # text_content has the label preserved, href stripped
    para = next(b for b in result.blocks if b.block_type == "paragraph")
    assert "x" in (para.text_content or "")
    assert "javascript" not in (para.text_content or ""), (
        f"unsafe href must be stripped from text_content, got {para.text_content!r}"
    )
    # stripped_links recorded
    stripped = para.payload_json.get("stripped_links", [])
    assert any(s["href"] == "javascript:alert(1)" for s in stripped), (
        f"javascript: must appear in stripped_links, got {stripped!r}"
    )
    # unsafe link does NOT appear in inline_marks (only safe links do)
    marks = para.payload_json.get("inline_marks", [])
    assert all(m.get("type") != "link" for m in marks), (
        f"unsafe link must not appear in inline_marks, got {marks!r}"
    )


def test_a3_html_inline_link_overlap_fail_closed() -> None:
    """html_inline + link overlap (data:text/html,<script>...) →
    fail-closed to candidate with inline_html warning. No "rescue" merge.
    """
    result = MarkdownSourceParser().parse(
        "[unsafe](data:text/html,<script>alert(1)</script>)"
    )
    warning_codes = {w.code for w in result.warnings}
    assert "inline_html" in warning_codes, (
        f"inline_html warning expected for html_inline+link overlap: {warning_codes!r}"
    )
    assert result.outcome == "candidate_document_required"
    # The html_inline tokens (<script>/</script>) must NOT appear in text_content
    para = next(b for b in result.blocks if b.block_type == "paragraph")
    text = para.text_content or ""
    assert "<script>" not in text, (
        f"html_inline content must be stripped from text_content, got {text!r}"
    )
    assert "</script>" not in text, (
        f"html_inline content must be stripped from text_content, got {text!r}"
    )


def test_a3_vbscript_unsafe_link_stripped() -> None:
    """[v](vbscript:msgbox(1)) → stripped, unsafe_link_protocol warning."""
    result = MarkdownSourceParser().parse("[v](vbscript:msgbox(1)) end")
    warning_codes = {w.code for w in result.warnings}
    assert "unsafe_link_protocol" in warning_codes
    assert result.outcome == "candidate_document_required"
    para = next(b for b in result.blocks if b.block_type == "paragraph")
    stripped = para.payload_json.get("stripped_links", [])
    assert any(s["href"] == "vbscript:msgbox(1)" for s in stripped), (
        f"vbscript: must appear in stripped_links, got {stripped!r}"
    )


def test_a3_mixed_safe_and_unsafe_links() -> None:
    """[ok](https://ok.com) and [bad](javascript:bad) →
    safe link in inline_marks + links; unsafe in stripped_links.
    """
    result = MarkdownSourceParser().parse(
        "[ok](https://ok.com) and [bad](javascript:bad)"
    )
    para = next(b for b in result.blocks if b.block_type == "paragraph")
    # safe link recorded in inline_marks
    marks = para.payload_json.get("inline_marks", [])
    safe_link_marks = [m for m in marks if m.get("type") == "link"]
    assert len(safe_link_marks) == 1, (
        f"expected 1 safe link mark, got {safe_link_marks!r}"
    )
    assert safe_link_marks[0]["href"] == "https://ok.com"
    # safe link also in payload_json.links
    assert any(
        link["href"] == "https://ok.com"
        for link in para.payload_json.get("links", [])
    )
    # unsafe link in stripped_links
    stripped = para.payload_json.get("stripped_links", [])
    assert any(s["href"] == "javascript:bad" for s in stripped), (
        f"javascript:bad must appear in stripped_links, got {stripped!r}"
    )


def test_a3_nested_parentheses_in_link_destination() -> None:
    """[a([b](url1)](url2) → merge semantics: outer link wins (or
    fail-closed if ambiguous). Either way, no crash, candidate or stable.
    """
    # This is a tricky CommonMark edge case. We just assert no crash
    # and the parser produces a deterministic result.
    result = MarkdownSourceParser().parse("[a([b](https://url1.com)](https://url2.com)")
    # Must produce some blocks and a deterministic outcome
    assert result.outcome in (
        "stable_document_ready",
        "candidate_document_required",
        "input_rejected_or_action_required",
    )
    assert len(result.blocks) >= 1


def test_a3_link_with_title_preserved_in_links() -> None:
    """[t](https://x.com "title") → safe link recorded (title may appear
    in links but inline_marks only carries href).
    """
    result = MarkdownSourceParser().parse('[t](https://x.com "title")')
    para = next(b for b in result.blocks if b.block_type == "paragraph")
    # safe link recorded
    links = para.payload_json.get("links", [])
    assert any(link["href"] == "https://x.com" for link in links), (
        f"https://x.com must appear in links, got {links!r}"
    )
    # inline_marks link mark has href (no title field)
    marks = para.payload_json.get("inline_marks", [])
    link_marks = [m for m in marks if m.get("type") == "link"]
    assert len(link_marks) == 1
    assert link_marks[0]["href"] == "https://x.com"
    assert "title" not in link_marks[0], (
        f"inline_marks link must not carry title, got {link_marks[0]!r}"
    )


def test_a3_html_truncated_scheme_no_rescue() -> None:
    """html_inline breaking a scheme (e.g. <a href="jav...) →
    fail-closed to candidate with inline_html warning.
    """
    # An html_inline that breaks a link scheme — should fail-closed
    result = MarkdownSourceParser().parse('<a href="jav`script:alert(1)">x</a>')
    warning_codes = {w.code for w in result.warnings}
    assert "inline_html" in warning_codes, (
        f"inline_html warning expected for html scheme break: {warning_codes!r}"
    )
    assert result.outcome == "candidate_document_required"


def test_a3_safe_links_do_not_emit_unsafe_warning() -> None:
    """[ok](https://ok.com) → no unsafe_link_protocol warning."""
    result = MarkdownSourceParser().parse("[ok](https://ok.com) and [m](mailto:x@y.com)")
    warning_codes = {w.code for w in result.warnings}
    assert "unsafe_link_protocol" not in warning_codes, (
        f"safe links must not trigger unsafe_link_protocol: {warning_codes!r}"
    )
    # mailto is also safe
    para = next(b for b in result.blocks if b.block_type == "paragraph")
    links = para.payload_json.get("links", [])
    hrefs = {link["href"] for link in links}
    assert "https://ok.com" in hrefs
    assert "mailto:x@y.com" in hrefs


def test_a3_relative_link_treated_as_safe() -> None:
    """Relative link (no scheme) → safe (existing _is_safe_link behavior)."""
    result = MarkdownSourceParser().parse("[rel](/path/to/page) end")
    para = next(b for b in result.blocks if b.block_type == "paragraph")
    # relative link should be in inline_marks and links
    marks = para.payload_json.get("inline_marks", [])
    link_marks = [m for m in marks if m.get("type") == "link"]
    assert len(link_marks) == 1, f"expected 1 link mark, got {link_marks!r}"
    assert link_marks[0]["href"] == "/path/to/page"
    # no unsafe warning
    warning_codes = {w.code for w in result.warnings}
    assert "unsafe_link_protocol" not in warning_codes


# ---------------------------------------------------------------------------
# A2 + A3 combined: unsafe link in inline context does not produce marks
# ---------------------------------------------------------------------------


def test_a2_a3_unsafe_link_text_preserved_no_mark() -> None:
    """Unsafe link label preserved in text_content, but no inline mark
    (mark would carry href we cannot safely expose).
    """
    block = _first_block("[bad](javascript:alert(1)) tail")
    # label "bad" preserved in text_content
    assert "bad" in (block.text_content or "")
    # no link mark for unsafe link
    marks = block.payload_json.get("inline_marks", [])
    assert all(m.get("type") != "link" for m in marks), (
        f"unsafe link must not produce inline mark, got {marks!r}"
    )


def test_a2_a3_link_mark_offsets_after_stripped_unsafe_link() -> None:
    """When unsafe link is stripped to label, subsequent safe link mark
    offsets must be correct (based on cleaned text_content).
    """
    # "[bad](javascript:alert(1)) and [ok](https://ok.com)"
    # After stripping: "bad and ok" → ok mark at [8, 10]
    block = _first_block("[bad](javascript:alert(1)) and [ok](https://ok.com)")
    text = block.text_content or ""
    assert text == "bad and ok", f"unexpected text_content: {text!r}"
    marks = block.payload_json.get("inline_marks", [])
    link_marks = [m for m in marks if m.get("type") == "link"]
    assert len(link_marks) == 1, f"expected 1 link mark, got {link_marks!r}"
    # "bad and ok" → "ok" is at utf16 offset [8, 10]
    expected_start = _utf16_len("bad and ")
    assert link_marks[0]["start"] == expected_start, (
        f"link start offset wrong: got {link_marks[0]['start']}, "
        f"expected {expected_start}"
    )
    assert link_marks[0]["end"] == expected_start + _utf16_len("ok")
