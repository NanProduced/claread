"""Source callout (<aside> / GFM alert) and reference list classification + reload tests.

These tests exercise the deterministic parser → classifier → policy pipeline
end-to-end with no LLM calls:

  1. Raw ``<aside>`` HTML block → ``source_callout`` (T-only policy).
  2. GFM alert marker ``> [!NOTE]`` → ``source_callout`` (T-only policy).
  3. Round-trip — editing a confirmed source and reparsing preserves source_callout.
  4. Escaped ``\\<aside>`` (Markdown backslash escape) stays as literal prose,
     NOT auto-unescape to ``source_callout``. User can recover by editing
     Confirmed Source to unescaped ``<aside>`` and reparsing.
  5. Reference list heading recognition (References / Reference list /
     Bibliography / Works cited) → ``citation_reference`` (T-only policy).
  6. Reference list entries containing URLs stay ``citation_reference``
     (low-disturbance T-only policy), never ``link_only``.
"""

from __future__ import annotations

import pytest

from app.services.reader_orchestration.automatic_layer_policy import (
    AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
    resolve_policy_for_stable_block,
)
from app.services.reader_orchestration.markdown_source_parser import MarkdownSourceParser
from app.services.reader_orchestration.semantic_classifier import (
    SEMANTIC_CONTRACT_V1,
    SOURCE_SEMANTIC_HINT_GFM_ALERT,
    SOURCE_SEMANTIC_HINT_HTML_ASIDE,
    annotate_blocks_with_semantic,
    classify_blocks,
)

_T_ONLY_POLICY = {
    "translation": True,
    "vocabulary": False,
    "grammar_note": False,
    "sentence_analysis": False,
}


def _role(block) -> str | None:
    semantic = (block.payload_json or {}).get("semantic") or {}
    return semantic.get("content_role")


def _assert_t_only(block) -> None:
    """Resolve the automatic layer policy for an annotated block and assert T-only."""
    resolved = resolve_policy_for_stable_block(
        block_type=block.block_type,
        payload_json=block.payload_json,
    )
    assert resolved.policy.as_dict() == _T_ONLY_POLICY
    assert resolved.contract_version == SEMANTIC_CONTRACT_V1
    assert resolved.resolver_version == AUTOMATIC_LAYER_POLICY_RESOLVER_V1


def _project(block) -> dict:
    """Project a ParsedBlock to the mapping shape ``classify_blocks`` expects."""
    return {
        "block_type": block.block_type,
        "text_content": block.text_content,
        "payload_json": block.payload_json or {},
    }


# ---------------------------------------------------------------------------
# Test 1: Raw <aside> HTML block → source_callout classification
# ---------------------------------------------------------------------------


def test_raw_aside_html_block_classifies_as_source_callout() -> None:
    md = "<aside>This is a callout</aside>"

    result = MarkdownSourceParser().parse(md)
    blocks = list(result.blocks)

    # The parser is the only writer of the stable hint; the classifier is the
    # only role seam that consumes it.
    hinted = [
        b
        for b in blocks
        if (b.payload_json or {}).get("source_semantic_hint")
        == SOURCE_SEMANTIC_HINT_HTML_ASIDE
    ]
    assert hinted, "parser must write source_semantic_hint=html_aside for <aside>"
    assert hinted[0].block_type == "blockquote"

    # Run the semantic classifier directly on projected blocks.
    classifications = classify_blocks([_project(b) for b in blocks])
    callout_cls = [c for c in classifications if c.content_role == "source_callout"]
    assert callout_cls, "classifier must assign content_role=source_callout to <aside>"

    # Annotated payload drives the automatic layer policy resolver.
    annotated = annotate_blocks_with_semantic(blocks)
    callouts = [b for b in annotated if _role(b) == "source_callout"]
    assert callouts, "annotated block must carry content_role=source_callout"
    for b in callouts:
        _assert_t_only(b)


# ---------------------------------------------------------------------------
# Test 2: GFM alert marker > [!NOTE] → source_callout classification
# ---------------------------------------------------------------------------


def test_gfm_alert_marker_classifies_as_source_callout() -> None:
    md = "> [!NOTE]\n> This is a note callout"

    result = MarkdownSourceParser().parse(md)
    annotated = annotate_blocks_with_semantic(list(result.blocks))

    callouts = [b for b in annotated if _role(b) == "source_callout"]
    assert callouts, "GFM alert > [!NOTE] must classify as source_callout"
    for b in callouts:
        _assert_t_only(b)


# ---------------------------------------------------------------------------
# Test 3: Round-trip — edit confirmed source → reparse preserves source_callout
# ---------------------------------------------------------------------------


def test_roundtrip_edit_confirmed_source_preserves_source_callout() -> None:
    original_md = "> [!NOTE]\n> Original callout content"

    first = annotate_blocks_with_semantic(
        list(MarkdownSourceParser().parse(original_md).blocks)
    )
    assert any(_role(b) == "source_callout" for b in first), (
        "original GFM alert must classify as source_callout before edit"
    )

    # Simulate a user edit to the confirmed source: only the body text changes,
    # the GFM marker is preserved. Reparsing must keep source_callout semantics.
    # This is the recovery path for escaped historical samples: the user can
    # edit the confirmed source to add the GFM marker, reparse, and get
    # source_callout semantics.
    edited_md = "> [!NOTE]\n> Edited callout content"
    reparsed = annotate_blocks_with_semantic(
        list(MarkdownSourceParser().parse(edited_md).blocks)
    )

    callouts = [b for b in reparsed if _role(b) == "source_callout"]
    assert callouts, "reparse of edited GFM alert must still classify as source_callout"
    for b in callouts:
        _assert_t_only(b)


# ---------------------------------------------------------------------------
# Test 4: Escaped \<aside> stays as literal prose (no auto-unescape);
#          user can recover by editing Confirmed Source to unescaped <aside>.
# ---------------------------------------------------------------------------


def test_escaped_aside_stays_literal_prose_not_auto_unescaped() -> None:
    # Markdown backslash escape: `\<` produces literal `<` text, NOT an HTML
    # block. markdown_it_py parses this as a paragraph with literal text
    # "<aside>...</aside>", no html_aside hint, no source_callout role.
    # This is the safety regression for historical escaped data (e.g. record
    # 8545eee4-9973-490b-93b5-41de58bec784): it must NOT be auto-unscape to
    # source_callout. The only recovery path is user edits Confirmed Source.
    md = r"\<aside>This is a callout\</aside>"

    result = MarkdownSourceParser().parse(md)
    blocks = list(result.blocks)

    # No block should carry the html_aside hint.
    hinted = [
        b
        for b in blocks
        if (b.payload_json or {}).get("source_semantic_hint")
        == SOURCE_SEMANTIC_HINT_HTML_ASIDE
    ]
    assert not hinted, (
        "escaped \\<aside> must not produce html_aside hint (no auto-unescape)"
    )

    # No block should classify as source_callout.
    annotated = annotate_blocks_with_semantic(blocks)
    callouts = [b for b in annotated if _role(b) == "source_callout"]
    assert not callouts, (
        "escaped \\<aside> must not classify as source_callout (stays literal prose)"
    )

    # The literal text must be preserved (not silently dropped).
    assert any(
        "<aside>" in (b.text_content or "") for b in blocks
    ), "escaped \\<aside> literal text must be preserved in text_content"


def test_user_edit_recovery_escaped_to_unescaped_aside() -> None:
    # User edits Confirmed Source: changes escaped `\<aside>` to unescaped
    # `<aside>`. Reparse must recognize it as html_block → html_aside hint →
    # source_callout (T-only). This is the user-controlled recovery path for
    # historical escaped data; no batch migration is performed.
    escaped_md = r"\<aside>This is a callout\</aside>"
    unescaped_md = "<aside>This is a callout</aside>"

    # Escaped form stays literal prose.
    escaped_annotated = annotate_blocks_with_semantic(
        list(MarkdownSourceParser().parse(escaped_md).blocks)
    )
    assert all(_role(b) != "source_callout" for b in escaped_annotated), (
        "escaped form must not be source_callout before user edit"
    )

    # After user edit to unescaped form, reparse recovers source_callout.
    recovered_annotated = annotate_blocks_with_semantic(
        list(MarkdownSourceParser().parse(unescaped_md).blocks)
    )
    callouts = [
        b for b in recovered_annotated if _role(b) == "source_callout"
    ]
    assert callouts, (
        "user edit to unescaped <aside> must recover source_callout via reparse"
    )
    for b in callouts:
        _assert_t_only(b)


# ---------------------------------------------------------------------------
# Test 5: Reference list title recognition
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "heading",
    [
        "References",
        "Reference list",
        "Reference list (APA 7)",
        "Bibliography",
        "Works cited",
    ],
)
def test_reference_list_heading_recognition(heading: str) -> None:
    md = (
        f"## {heading}\n"
        "- [1] Smith, J. (2020). A study of reading comprehension.\n"
        "- [2] Doe, J. (2021). Another reference work on literacy."
    )

    result = MarkdownSourceParser().parse(md)
    annotated = annotate_blocks_with_semantic(list(result.blocks))

    ref_items = [b for b in annotated if _role(b) == "citation_reference"]
    assert ref_items, (
        f"list_items under heading {heading!r} must classify as citation_reference"
    )
    assert len(ref_items) >= 2, "both reference entries must be classified"
    for b in ref_items:
        _assert_t_only(b)


# ---------------------------------------------------------------------------
# Test 6: Reference list with URL — low-disturbance policy
# ---------------------------------------------------------------------------


def test_reference_list_with_urls_low_disturbance_policy() -> None:
    md = (
        "## References\n"
        "- [Smith 2020](https://example.com/smith2020) A study of reading comprehension.\n"
        "- [Doe 2021](https://example.com/doe2021) Another reference work on literacy."
    )

    result = MarkdownSourceParser().parse(md)
    annotated = annotate_blocks_with_semantic(list(result.blocks))

    ref_items = [b for b in annotated if _role(b) == "citation_reference"]
    assert ref_items, (
        "reference entries with URLs must classify as citation_reference, not link_only"
    )
    assert len(ref_items) >= 2
    for b in ref_items:
        assert _role(b) != "link_only"
        _assert_t_only(b)


# ---------------------------------------------------------------------------
# R-Aside-1R Trailing text after </aside> must NOT be swallowed into callout
# ---------------------------------------------------------------------------


def test_trailing_text_after_closing_aside_same_line_becomes_separate_paragraph() -> None:
    """R-Aside-1R `</aside>Peer discussion...` on the same line must split.

    The aside callout block must contain only the inner content; the trailing
    prose must become a separate paragraph block. The old implementation
    concatenated them into a single text_content via ``_strip_html_tags`` +
    ``" ".join(agg_texts)``.
    """
    md = "<aside>Callout body</aside>Peer discussion continues here"

    result = MarkdownSourceParser().parse(md)
    blocks = list(result.blocks)

    # Exactly two blocks expected: callout + trailing paragraph.
    assert len(blocks) == 2, (
        f"expected 2 blocks (callout + trailing paragraph), got {len(blocks)}: "
        f"{[(b.block_type, b.text_content) for b in blocks]}"
    )

    callout, trailing = blocks

    # First block: the source_callout.
    assert (callout.payload_json or {}).get("source_semantic_hint") == (
        SOURCE_SEMANTIC_HINT_HTML_ASIDE
    ), "first block must carry html_aside hint"
    assert callout.block_type == "blockquote"
    assert callout.text_content == "Callout body", (
        f"callout text_content must be 'Callout body', got {callout.text_content!r}"
    )
    assert "Peer discussion" not in (callout.text_content or ""), (
        "trailing text must NOT leak into callout text_content"
    )

    # Second block: the trailing prose paragraph.
    assert trailing.block_type == "paragraph"
    assert trailing.text_content == "Peer discussion continues here", (
        f"trailing paragraph must preserve full text, got {trailing.text_content!r}"
    )
    assert (trailing.payload_json or {}).get("source_semantic_hint") is None, (
        "trailing paragraph must NOT carry html_aside hint"
    )

    # Policy: callout is T-only, trailing paragraph is default.
    annotated = annotate_blocks_with_semantic(blocks)
    callouts = [b for b in annotated if _role(b) == "source_callout"]
    assert len(callouts) == 1
    _assert_t_only(callouts[0])


def test_trailing_text_after_multiline_aside_becomes_separate_paragraph() -> None:
    """R-Aside-1R multiline aside with trailing text on closing line.

    Input:
        <aside>
        **Alignment**: body text.

        Second paragraph.
        </aside>Peer discussion continues

    R-Aside-1R2 update: the callout is now a structural container
    (blockquote, text_content=None) with child paragraphs parented to it.
    The trailing ``Peer discussion continues`` on the ``</aside>`` line
    must still become a separate paragraph block, parented to the document
    root (NOT to the callout container).
    """
    md = (
        "<aside>\n"
        "**Alignment**: body text.\n"
        "\n"
        "Second paragraph.\n"
        "</aside>Peer discussion continues"
    )

    result = MarkdownSourceParser().parse(md)
    blocks = list(result.blocks)

    # Find the callout container (blockquote with html_aside hint).
    callout = next(
        (
            b
            for b in blocks
            if (b.payload_json or {}).get("source_semantic_hint")
            == SOURCE_SEMANTIC_HINT_HTML_ASIDE
        ),
        None,
    )
    assert callout is not None, (
        f"callout container not found: {[(b.block_type, b.payload_json) for b in blocks]}"
    )
    assert callout.block_type == "blockquote"
    # Container is structural (no flattened text_content).
    assert not (callout.text_content or "").strip(), (
        f"callout container must not carry flattened text_content, "
        f"got {callout.text_content!r}"
    )

    # Inner paragraphs parented to the callout (body text + Second paragraph).
    inner = [
        b
        for b in blocks
        if b.block_type == "paragraph" and b.parent_block_id == callout.block_id
    ]
    assert len(inner) == 2, (
        "expected 2 inner paragraphs parented to callout, "
        f"got {len(inner)}: "
        f"{[(b.block_id, b.parent_block_id, b.text_content) for b in blocks if b.block_type == 'paragraph']}"  # noqa: E501
    )
    inner_texts = sorted(b.text_content or "" for b in inner)
    assert "Second paragraph." in inner_texts
    assert any("body text" in t for t in inner_texts)
    # None of the inner paragraphs carry the trailing text.
    assert all("Peer discussion" not in (b.text_content or "") for b in inner), (
        "trailing text must NOT leak into callout inner paragraphs"
    )

    # Trailing paragraph is independent (NOT parented to callout).
    trailing = next(
        (
            b
            for b in blocks
            if b.block_type == "paragraph"
            and "Peer discussion" in (b.text_content or "")
        ),
        None,
    )
    assert trailing is not None, "trailing paragraph must exist"
    assert trailing.parent_block_id is None, (
        f"trailing paragraph must NOT be parented to callout, "
        f"got parent={trailing.parent_block_id}"
    )
    assert trailing.text_content == "Peer discussion continues", (
        f"trailing paragraph text mismatch: {trailing.text_content!r}"
    )


def test_complete_aside_no_trailing_text_unchanged() -> None:
    """R-Aside-1R regression: `<aside>...</aside>` with no trailing text
    must still produce exactly one callout block (no empty trailing paragraph).
    """
    md = "<aside>Just a callout</aside>"

    result = MarkdownSourceParser().parse(md)
    blocks = list(result.blocks)

    assert len(blocks) == 1, (
        f"expected 1 block, got {len(blocks)}: "
        f"{[(b.block_type, b.text_content) for b in blocks]}"
    )
    callout = blocks[0]
    assert (callout.payload_json or {}).get("source_semantic_hint") ==(
        SOURCE_SEMANTIC_HINT_HTML_ASIDE
    )
    assert callout.text_content == "Just a callout"


def test_aside_kind_attributes_stripped_from_payload() -> None:
    """R-Aside-1R B: aside with class/data attributes must NOT persist them.

    The canonical aside does not carry kind; only `class` is consumed for
    kind inference (currently unused since kind is unified to note), and all
    other attributes (onclick, style, href, src, data-*) must be stripped
    before reaching the stable payload.
    """
    md = (
        '<aside class="callout-warning" onclick="alert(1)" '
        'style="color:red" data-track="evil">Body</aside>'
    )

    result = MarkdownSourceParser().parse(md)
    blocks = list(result.blocks)

    assert len(blocks) == 1
    callout = blocks[0]
    payload = callout.payload_json or {}

    # Semantic hint preserved.
    assert payload.get("source_semantic_hint") == SOURCE_SEMANTIC_HINT_HTML_ASIDE

    # No kind / class / dangerous attributes leak into payload.
    assert "kind" not in payload, "kind must NOT be persisted in canonical payload"
    assert "class" not in payload, "class must NOT be persisted in payload"
    assert "onclick" not in payload
    assert "style" not in payload
    assert "data-track" not in payload

    # Text content is just the body.
    assert callout.text_content == "Body"


# ---------------------------------------------------------------------------
# R-Aside-1R2: Structured aside internal Markdown must preserve block tree +
# inline_marks (RED test for old flat-join implementation).
#
# Old implementation: `_strip_html_tags + " ".join(agg_texts)` flattened
# aside internal Markdown (strong/em/link, paragraphs, lists) into a single
# plain-text leaf. Strong/em/link stayed as raw `**...**`/`*...*`/`[...](...)`
# characters, paragraphs were joined by spaces, and the list became orphaned
# with no parent relationship to the callout.
#
# Required structure (per task spec):
#   - callout container block (blockquote, source_semantic_hint=html_aside,
#     text_content=None or empty) with child blocks parented to it.
#   - strong/em/link become payload_json.inline_marks on the child paragraph
#     (NOT raw `**...**`/`*...*`/`[...](...)` characters in text_content).
#   - two paragraphs + list survive as children with correct order.
#   - trailing `Peer discussion continues.` is an independent paragraph
#     (no html_aside hint, parent=None).
# ---------------------------------------------------------------------------


STRUCTURED_ASIDE_MD = (
    "<aside>\n"
    "**Alignment**: *emphasis*, `code` and [a link](https://example.com).\n"
    "\n"
    "Second paragraph.\n"
    "\n"
    "- First item\n"
    "- Second item\n"
    "</aside>Peer discussion continues."
)

RICH_HTML_ASIDE = (
    '<aside class="notion-callout" onclick="alert(1)">'
    "<p><strong>Alignment</strong>: <em>emphasis</em>, <code>code</code> "
    'and <a href="https://example.com">a link</a>.</p>'
    "<p>Second paragraph.</p>"
    "<ul><li>First item</li><li>Second item</li></ul>"
    "</aside>"
    "After the rich aside."
)


def test_rich_html_aside_preserves_children_marks_and_trailing_text() -> None:
    """G1: Notion-style HTML aside uses the same Stable block shape as Markdown."""
    result = MarkdownSourceParser().parse(RICH_HTML_ASIDE)
    blocks = list(result.blocks)
    callout = next(
        b
        for b in blocks
        if (b.payload_json or {}).get("source_semantic_hint") == SOURCE_SEMANTIC_HINT_HTML_ASIDE
    )
    assert callout.block_type == "blockquote"
    assert not (callout.text_content or "").strip()

    children = [b for b in blocks if b.parent_block_id == callout.block_id]
    assert [b.block_type for b in children] == ["paragraph", "paragraph", "list"]
    assert children[0].text_content == "Alignment: emphasis, code and a link."
    mark_types = {
        mark["type"] for mark in (children[0].payload_json or {}).get("inline_marks", [])
    }
    assert {"strong", "em", "inline_code", "link"} <= mark_types
    assert (children[0].payload_json or {}).get("links") == [
        {"text": "a link", "href": "https://example.com"}
    ]
    list_block = children[2]
    items = [b for b in blocks if b.parent_block_id == list_block.block_id]
    assert [b.text_content for b in items] == ["First item", "Second item"]
    trailing = next(b for b in blocks if "After the rich aside." in (b.text_content or ""))
    assert trailing.parent_block_id is None
    assert (trailing.payload_json or {}).get(
        "source_semantic_hint"
    ) != SOURCE_SEMANTIC_HINT_HTML_ASIDE


def test_rich_html_aside_drops_dangerous_elements_and_unsafe_href() -> None:
    """G1: rich HTML keeps visible safe text but never executable markup."""
    result = MarkdownSourceParser().parse(
        '<aside><p>Keep <script>alert(1)</script><strong>text</strong> '
        '<iframe>frame content</iframe><a href="javascript:alert(1)">unsafe</a>'
        "</p></aside>"
    )
    callout = next(
        b
        for b in result.blocks
        if (b.payload_json or {}).get("source_semantic_hint")
        == SOURCE_SEMANTIC_HINT_HTML_ASIDE
    )
    child = next(
        b for b in result.blocks if b.parent_block_id == callout.block_id
    )
    assert child.text_content == "Keep text unsafe"
    assert "alert" not in (child.text_content or "")
    assert "frame" not in (child.text_content or "")
    mark_types = {
        mark["type"]
        for mark in (child.payload_json or {}).get("inline_marks", [])
    }
    assert "strong" in mark_types
    assert "link" not in mark_types
    assert any(w.code == "unsafe_link_protocol" for w in result.warnings)


def test_structured_aside_preserves_block_tree_and_inline_marks() -> None:
    """R-Aside-1R2: structured aside must preserve internal Markdown structure.

    The aside internal content (strong/em/link/paragraphs/list) must be
    parsed as child blocks parented to the callout container, with inline
    marks captured in payload_json.inline_marks. The old flat-join path
    (``_strip_html_tags + " ".join(agg_texts)``) is forbidden.
    """
    result = MarkdownSourceParser().parse(STRUCTURED_ASIDE_MD)
    blocks = list(result.blocks)

    # --- 1. Callout container block exists with html_aside hint ---
    callout = next(
        (
            b
            for b in blocks
            if (b.payload_json or {}).get("source_semantic_hint")
            == SOURCE_SEMANTIC_HINT_HTML_ASIDE
        ),
        None,
    )
    assert callout is not None, (
        "parser must emit a callout container carrying "
        "source_semantic_hint=html_aside; got blocks: "
        f"{[(b.block_type, b.payload_json) for b in blocks]}"
    )
    assert callout.block_type == "blockquote", (
        f"callout container must be blockquote, got {callout.block_type}"
    )
    # Container is structural (text_content None or empty); narrative text
    # lives in child blocks.
    assert not (callout.text_content or "").strip(), (
        f"callout container must not carry flattened text_content, "
        f"got {callout.text_content!r}"
    )

    # --- 2. Two paragraphs inside the callout, parented to it ---
    inner_paragraphs = [
        b
        for b in blocks
        if b.block_type == "paragraph"
        and b.parent_block_id == callout.block_id
        and (b.payload_json or {}).get("extracted_from") != "html_block_trailing"
    ]
    assert len(inner_paragraphs) == 2, (
        "expected 2 inner paragraphs parented to callout, "
        f"got {len(inner_paragraphs)}: "
        f"{[(b.block_id, b.parent_block_id, b.text_content) for b in blocks if b.block_type == 'paragraph']}"  # noqa: E501
    )

    # First inner paragraph carries the strong/em/code/link inline marks.
    first_para = next(
        p for p in inner_paragraphs if "Alignment" in (p.text_content or "")
    )
    marks = (first_para.payload_json or {}).get("inline_marks") or []
    mark_types = sorted(m["type"] for m in marks)
    assert "strong" in mark_types, (
        f"first inner paragraph must carry a strong inline mark; "
        f"got marks={marks}, text={first_para.text_content!r}"
    )
    assert "em" in mark_types, (
        f"first inner paragraph must carry an em inline mark; "
        f"got marks={marks}, text={first_para.text_content!r}"
    )
    assert "inline_code" in mark_types, (
        f"first inner paragraph must carry an inline_code mark; "
        f"got marks={marks}, text={first_para.text_content!r}"
    )
    assert "link" in mark_types, (
        f"first inner paragraph must carry a link inline mark; "
        f"got marks={marks}, text={first_para.text_content!r}"
    )
    # Link mark must carry href.
    link_mark = next(m for m in marks if m["type"] == "link")
    assert link_mark.get("href") == "https://example.com", (
        f"link mark href must be https://example.com, got {link_mark}"
    )
    # Strong/em/link must NOT survive as raw characters in text_content.
    assert "**" not in (first_para.text_content or ""), (
        "strong must be in inline_marks, not raw '**' in text_content"
    )
    assert "*emphasis*" not in (first_para.text_content or ""), (
        "em must be in inline_marks, not raw '*...*' in text_content"
    )
    assert "](" not in (first_para.text_content or ""), (
        "link must be in inline_marks, not raw '[...](...)' in text_content"
    )

    # Second inner paragraph is plain text.
    second_para = next(
        p for p in inner_paragraphs if "Second paragraph" in (p.text_content or "")
    )
    assert second_para.text_content == "Second paragraph."

    # --- 3. List inside the callout, parented to it ---
    inner_lists = [
        b
        for b in blocks
        if b.block_type == "list" and b.parent_block_id == callout.block_id
    ]
    assert len(inner_lists) == 1, (
        "expected 1 list parented to callout, "
        f"got {len(inner_lists)}: "
        f"{[(b.block_id, b.parent_block_id, b.block_type) for b in blocks if b.block_type == 'list']}"  # noqa: E501
    )
    inner_list = inner_lists[0]

    list_items = [
        b
        for b in blocks
        if b.block_type == "list_item" and b.parent_block_id == inner_list.block_id
    ]
    assert len(list_items) == 2, (
        f"expected 2 list_items parented to inner list, got {len(list_items)}"
    )
    item_texts = sorted(b.text_content or "" for b in list_items)
    assert item_texts == ["First item", "Second item"], (
        f"list_item texts mismatch: {item_texts}"
    )

    # --- 4. Trailing paragraph is independent (NOT inside callout) ---
    trailing = next(
        (
            b
            for b in blocks
            if b.block_type == "paragraph"
            and "Peer discussion" in (b.text_content or "")
        ),
        None,
    )
    assert trailing is not None, "trailing paragraph 'Peer discussion...' must exist"
    assert trailing.parent_block_id is None, (
        f"trailing paragraph must NOT be parented to callout, "
        f"got parent={trailing.parent_block_id}"
    )
    assert (trailing.payload_json or {}).get("source_semantic_hint") != (
        SOURCE_SEMANTIC_HINT_HTML_ASIDE
    ), "trailing paragraph must NOT carry html_aside hint"
    assert trailing.text_content == "Peer discussion continues."

    # --- 5. No block carries raw '**', '*[' or '](' as flat text from aside ---
    # (only the trailing paragraph and inner paragraphs may exist; none should
    # contain raw Markdown syntax characters from the aside body).
    flat污染 = [
        b
        for b in blocks
        if (b.text_content or "")
        and (
            "**Alignment**" in b.text_content
            or "*emphasis*" in b.text_content
            or "](" in b.text_content
        )
    ]
    assert not flat污染, (
        f"aside internal Markdown must not survive as raw characters; "
        f"offending blocks: {[(b.block_id, b.text_content) for b in flat污染]}"
    )


def test_structured_aside_callout_classifies_as_source_callout_t_only() -> None:
    """R-Aside-1R2: structured aside callout + children classify correctly.

    The callout container and every text-bearing descendant carry the
    source_callout role with T-only automatic layer policy. Structural list
    wrappers remain role-null and retain their Stable tree shape.
    """
    result = MarkdownSourceParser().parse(STRUCTURED_ASIDE_MD)
    annotated = annotate_blocks_with_semantic(list(result.blocks))

    callout = next(b for b in annotated if b.block_type == "blockquote")
    _assert_t_only(callout)

    # Text-bearing descendants inherit source_callout and T-only policy.
    children = [
        b
        for b in annotated
        if b.parent_block_id == callout.block_id
        or any(
            parent.block_id == b.parent_block_id
            and parent.parent_block_id == callout.block_id
            for parent in annotated
        )
    ]
    assert children
    for child in children:
        if child.block_type in {"paragraph", "list_item"}:
            assert _role(child) == "source_callout"
            _assert_t_only(child)


def test_structured_aside_dangerous_attributes_stripped() -> None:
    """R-Aside-1R2: structured aside with dangerous attributes must strip them.

    `<aside onclick=... style=... data-*=...>` must keep html_aside hint and
    body text, but strip all attributes from the canonical payload. Uses
    multi-line input (blank line before ``</aside>``) so the container mode
    path is exercised — self-contained ``<aside>...</aside>`` on a single
    line stays on the existing flat-path (covered by the existing test
    ``test_aside_kind_attributes_stripped_from_payload``).
    """
    md = (
        '<aside class="callout-warning" onclick="alert(1)" '
        'style="color:red" data-track="evil">\n'
        "**Body** with marks.\n"
        "\n"
        "</aside>"
    )
    result = MarkdownSourceParser().parse(md)
    blocks = list(result.blocks)

    callout = next(
        (
            b
            for b in blocks
            if (b.payload_json or {}).get("source_semantic_hint")
            == SOURCE_SEMANTIC_HINT_HTML_ASIDE
        ),
        None,
    )
    assert callout is not None, "dangerous-attribute aside must still carry html_aside hint"
    payload = callout.payload_json or {}
    for forbidden in ("kind", "class", "onclick", "style", "data-track", "href", "src"):
        assert forbidden not in payload, (
            f"dangerous attribute {forbidden!r} must NOT leak into payload: {payload}"
        )

    # Inner paragraph carries strong inline mark (structure preserved).
    inner = [
        b
        for b in blocks
        if b.block_type == "paragraph" and b.parent_block_id == callout.block_id
    ]
    assert inner, "dangerous-attribute aside must still preserve inner paragraph"
    marks = (inner[0].payload_json or {}).get("inline_marks") or []
    assert any(m["type"] == "strong" for m in marks), (
        f"inner paragraph must carry strong mark; got {marks}"
    )


# ---------------------------------------------------------------------------
# R-Aside-1R2: GFM alert structural container — internal Markdown structure
# (paragraphs / lists / inline marks) must survive as child blocks, not be
# flattened into a single text_content string.
# ---------------------------------------------------------------------------


STRUCTURED_GFM_ALERT_MD = (
    "> [!NOTE]\n"
    "> **Alignment**: *emphasis*, `code` and [a link](https://example.com).\n"
    ">\n"
    "> Second paragraph.\n"
    ">\n"
    "> - First item\n"
    "> - Second item\n"
)


def test_structured_gfm_alert_preserves_block_tree_and_inline_marks() -> None:
    """R-Aside-1R2: GFM alert must preserve internal Markdown structure.

    The GFM alert blockquote becomes a structural container with
    ``source_semantic_hint=gfm_alert``. Internal paragraphs, lists and
    inline marks survive as child blocks parented to the container.
    """
    result = MarkdownSourceParser().parse(STRUCTURED_GFM_ALERT_MD)
    blocks = list(result.blocks)

    # --- 1. Callout container exists with gfm_alert hint ---
    callout = next(
        (
            b
            for b in blocks
            if (b.payload_json or {}).get("source_semantic_hint")
            == SOURCE_SEMANTIC_HINT_GFM_ALERT
        ),
        None,
    )
    assert callout is not None, (
        "parser must emit a callout container carrying "
        "source_semantic_hint=gfm_alert; got blocks: "
        f"{[(b.block_type, b.payload_json) for b in blocks]}"
    )
    assert callout.block_type == "blockquote"
    assert not (callout.text_content or "").strip(), (
        f"gfm_alert container must not carry flattened text_content, "
        f"got {callout.text_content!r}"
    )

    # --- 2. Two paragraphs inside the callout ---
    inner_paragraphs = [
        b
        for b in blocks
        if b.block_type == "paragraph" and b.parent_block_id == callout.block_id
    ]
    assert len(inner_paragraphs) == 2, (
        f"expected 2 inner paragraphs, got {len(inner_paragraphs)}: "
        f"{[(b.block_id, b.text_content) for b in inner_paragraphs]}"
    )

    # First inner paragraph carries strong/em/code/link inline marks.
    first_para = next(
        p for p in inner_paragraphs if "Alignment" in (p.text_content or "")
    )
    marks = (first_para.payload_json or {}).get("inline_marks") or []
    mark_types = sorted(m["type"] for m in marks)
    assert "strong" in mark_types, f"first paragraph must carry strong; got {marks}"
    assert "em" in mark_types, f"first paragraph must carry em; got {marks}"
    assert "inline_code" in mark_types, (
        f"first paragraph must carry inline_code; got {marks}"
    )
    assert "link" in mark_types, f"first paragraph must carry link; got {marks}"
    link_mark = next(m for m in marks if m["type"] == "link")
    assert link_mark.get("href") == "https://example.com"

    # Second paragraph is plain text.
    second_para = next(
        p for p in inner_paragraphs if "Second paragraph" in (p.text_content or "")
    )
    assert second_para.text_content == "Second paragraph."

    # --- 3. List inside the callout ---
    inner_lists = [
        b
        for b in blocks
        if b.block_type == "list" and b.parent_block_id == callout.block_id
    ]
    assert len(inner_lists) == 1
    inner_list = inner_lists[0]
    list_items = [
        b
        for b in blocks
        if b.block_type == "list_item" and b.parent_block_id == inner_list.block_id
    ]
    assert len(list_items) == 2
    item_texts = sorted(b.text_content or "" for b in list_items)
    assert item_texts == ["First item", "Second item"]


def test_structured_gfm_alert_classifies_as_source_callout_t_only() -> None:
    """R-Aside-1R2: structural GFM alert container classifies as source_callout.

    The container and text-bearing descendants get content_role=source_callout
    via the ``gfm_alert`` hint. Structural wrappers remain role-null.
    """
    result = MarkdownSourceParser().parse(STRUCTURED_GFM_ALERT_MD)
    annotated = annotate_blocks_with_semantic(list(result.blocks))

    callout = next(b for b in annotated if b.block_type == "blockquote")
    _assert_t_only(callout)

    # Text-bearing descendants inherit source_callout and T-only policy.
    children = [
        b
        for b in annotated
        if b.parent_block_id == callout.block_id
        or any(
            parent.block_id == b.parent_block_id
            and parent.parent_block_id == callout.block_id
            for parent in annotated
        )
    ]
    assert children
    for child in children:
        if child.block_type in {"paragraph", "list_item"}:
            assert _role(child) == "source_callout"
            _assert_t_only(child)


def test_ordinary_blockquote_stays_flat_no_regression() -> None:
    """R-Aside-1R2: ordinary blockquote (no GFM marker) must NOT become a
    structural container. It keeps the existing flat path (single block with
    text_content + inline_marks) so quotations / reference lists do not
    regress.
    """
    md = "> This is an ordinary quotation.\n> With a second line."
    result = MarkdownSourceParser().parse(md)
    blocks = list(result.blocks)

    bq_blocks = [b for b in blocks if b.block_type == "blockquote"]
    assert len(bq_blocks) == 1
    bq = bq_blocks[0]
    # Flat path: text_content is non-empty, no source_semantic_hint.
    assert (bq.text_content or "").strip(), (
        "ordinary blockquote must keep flat text_content"
    )
    assert (bq.payload_json or {}).get("source_semantic_hint") is None, (
        "ordinary blockquote must NOT carry gfm_alert/html_aside hint"
    )
    # No child blocks parented to it.
    children = [b for b in blocks if b.parent_block_id == bq.block_id]
    assert not children, (
        f"ordinary blockquote must not have children (flat path); "
        f"got {[(b.block_id, b.block_type) for b in children]}"
    )


# ---------------------------------------------------------------------------
# R-Aside-1R2: Safety regression — dangerous tags, unclosed aside, attributes
# ---------------------------------------------------------------------------


def test_script_tag_inside_aside_stripped_not_executable() -> None:
    """R-Aside-1R2: ``<script>`` inside aside must be stripped, never
    executable, never enter the Reader as a tag. The aside hint is preserved
    (it is still a callout); only the tag structure is removed.
    """
    md = "<aside>\n<script>alert(1)</script>\n</aside>"
    result = MarkdownSourceParser().parse(md)
    blocks = list(result.blocks)

    # No block may carry a raw ``<script>`` tag in text_content or payload.
    for b in blocks:
        text = b.text_content or ""
        payload_str = str(b.payload_json or "")
        assert "<script" not in text.lower(), (
            f"script tag must NOT survive in text_content: {text!r}"
        )
        assert "<script" not in payload_str.lower(), (
            f"script tag must NOT survive in payload: {payload_str!r}"
        )
        assert "</script" not in text.lower()
        assert "</script" not in payload_str.lower()

    # The document must route to content_check or adaptation_notice
    # (has_raw_html flag set), never stable_document_ready unchecked.
    assert result.outcome in (
        "candidate_document_required",
        "stable_document_ready",
    ), (
        f"script-in-aside outcome must be gated; got {result.outcome}"
    )
    # At least one warning must mention raw HTML.
    assert any("raw html" in w.message.lower() or "html" in w.message.lower()
               for w in result.warnings), (
        f"expected an HTML-related warning; got {[w.message for w in result.warnings]}"
    )


def test_iframe_tag_inside_aside_stripped_not_executable() -> None:
    """R-Aside-1R2: ``<iframe>`` inside aside must be stripped, never enter
    the Reader as a tag.
    """
    md = '<aside>\n<iframe src="https://evil.example.com"></iframe>\n</aside>'
    result = MarkdownSourceParser().parse(md)
    blocks = list(result.blocks)

    for b in blocks:
        text = b.text_content or ""
        payload_str = str(b.payload_json or "")
        assert "<iframe" not in text.lower(), (
            f"iframe tag must NOT survive in text_content: {text!r}"
        )
        assert "<iframe" not in payload_str.lower()
        assert "</iframe" not in text.lower()
        assert "</iframe" not in payload_str.lower()


def test_unclosed_aside_degrades_safely() -> None:
    """An unclosed ``<aside>`` must not swallow following source blocks."""
    md = "<aside>\n**Bold** text.\n\nSecond paragraph."
    result = MarkdownSourceParser().parse(md)
    blocks = list(result.blocks)

    # No callout container may be created without a matching close tag.
    assert not any(
        (b.payload_json or {}).get("source_semantic_hint")
        == SOURCE_SEMANTIC_HINT_HTML_ASIDE
        for b in blocks
    )

    # Both paragraphs remain visible at the root; neither is swallowed by a
    # synthetic parent. Markdown marks are still normalized deterministically.
    paragraphs = [b for b in blocks if b.block_type == "paragraph"]
    assert [b.parent_block_id for b in paragraphs] == [None, None]
    assert [b.text_content for b in paragraphs] == ["Bold text.", "Second paragraph."]
    assert any(w.code == "unclosed_html_aside" for w in result.warnings)
    assert result.outcome == "candidate_document_required"


def test_escaped_aside_remains_literal_text() -> None:
    """R-Aside-1R2: ``\\<aside>`` (backslash-escaped) must stay as literal
    prose — no html_aside hint, no source_callout role. This is the safety
    regression for historical escaped data (e.g. record
    8545eee4-9973-490b-93b5-41de58bec784).
    """
    md = r"\<aside>This is a callout\</aside>"
    result = MarkdownSourceParser().parse(md)
    blocks = list(result.blocks)

    # No block should carry the html_aside hint.
    hinted = [
        b
        for b in blocks
        if (b.payload_json or {}).get("source_semantic_hint")
        == SOURCE_SEMANTIC_HINT_HTML_ASIDE
    ]
    assert not hinted, "escaped \\<aside> must not produce html_aside hint"

    # The literal text must be preserved.
    assert any(
        "<aside>" in (b.text_content or "") for b in blocks
    ), "escaped \\<aside> literal text must be preserved"

    # No block should classify as source_callout.
    annotated = annotate_blocks_with_semantic(blocks)
    callouts = [b for b in annotated if _role(b) == "source_callout"]
    assert not callouts, (
        "escaped \\<aside> must not classify as source_callout"
    )


def test_aside_dangerous_attributes_stripped_body_preserved() -> None:
    """R-Aside-1R2: ``<aside onclick=... style=... src=... href=... data-*=...>``
    must strip ALL attributes from the payload while preserving the body text
    and html_aside hint. This is the multi-line container path.
    """
    md = (
        '<aside class="warn" onclick="alert(1)" style="color:red" '
        'src="https://evil.example.com" href="javascript:evil" '
        'data-track="evil">\n'
        "**Body** with marks.\n"
        "\n"
        "</aside>"
    )
    result = MarkdownSourceParser().parse(md)
    blocks = list(result.blocks)

    callout = next(
        (
            b
            for b in blocks
            if (b.payload_json or {}).get("source_semantic_hint")
            == SOURCE_SEMANTIC_HINT_HTML_ASIDE
        ),
        None,
    )
    assert callout is not None, "dangerous-attribute aside must keep html_aside hint"
    payload = callout.payload_json or {}
    for forbidden in (
        "kind", "class", "onclick", "style", "src", "href",
        "data-track", "javascript", "evil",
    ):
        assert forbidden not in payload, (
            f"dangerous attribute {forbidden!r} must NOT leak into payload: {payload}"
        )
        assert forbidden not in str(payload).lower(), (
            f"dangerous attribute {forbidden!r} must NOT appear in payload string"
        )

    # Inner paragraph carries strong inline mark (structure preserved).
    inner = [
        b
        for b in blocks
        if b.block_type == "paragraph" and b.parent_block_id == callout.block_id
    ]
    assert inner, "dangerous-attribute aside must still preserve inner paragraph"
    marks = (inner[0].payload_json or {}).get("inline_marks") or []
    assert any(m["type"] == "strong" for m in marks), (
        f"inner paragraph must carry strong mark; got {marks}"
    )
