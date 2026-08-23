"""G2a-A · Markdown 图片 typed representation + source_url provenance 合同测试。

设计依据：tmp/reader-markdown-optimization/g2a-image-representation-contract.md
（合同收口版 §5/§6/§6.5/§7/§7.5/§12 的 G2a-A 部分）。

覆盖七组缺口（RED-first）：
  A. provenance —— unsafe / raw backslash / raw space / reference 语义 destination；
  B. representation —— standalone / inline / 容器 / 分隔空格 / notice；
  C. carrier —— image-only table_cell 显式 metadata_only policy 贯通；
  D. routing —— 只因图片存在不再进入 Content Check；
  E. ordinary link 基线 —— 与实现前逐字段一致的零回归锁定（§12 #22）。
  F. identity —— parser / normalizer v2 身份；
  G. persistence —— candidate blocks_json → confirm → freeze/readback。
"""

from __future__ import annotations

from typing import Any

import asyncpg
import pytest
from markdown_it.token import Token

from app.schemas.reader_input_adapter import InputSuitabilityRequest
from app.services.reader_orchestration.input_document_normalizer import (
    NORMALIZER_VERSION,
    normalize_input_document,
)
from app.services.reader_orchestration.input_suitability_gate import (
    evaluate_input_suitability,
)
from app.services.reader_orchestration.markdown_source_parser import (
    PARSER_VERSION,
    MarkdownSourceParseError,
    MarkdownSourceParser,
    _image_semantic_destination,
)
from tests.test_confirmed_source_lifecycle_db import (
    _confirm,
    _create_candidate,
    _insert_user,
    _json,
)

pytest_plugins = ("tests.test_confirmed_source_lifecycle_db",)

_BS = "\\"
_TABLE_CELL_METADATA_ONLY_POLICY: dict[str, Any] = {
    "allowed_source_scope": ["table_cell"],
    "default_route": "metadata_only",
    "rag_eligible": False,
}

# 用于 gate / normalizer 测试的合法英文正文（≥50 词、ratio 合格）。
_ENGLISH_PARAGRAPH = (
    "The committee reviewed the regional pilot results and recorded every "
    "measured outcome before drafting the summary for the public review "
    "session scheduled next month in the main hall near the river. "
    "Participants agreed that the appendix should remain available to every "
    "reader before the final vote takes place, and the editors promised to "
    "publish the complete dataset together with the annotated methodology "
    "section so that anyone can verify each recorded number independently."
)


def _parse(text: str):
    return MarkdownSourceParser().parse(text)


def _image_payload(block) -> dict[str, Any]:
    return block.payload_json


def _blocks_by_type(result, block_type: str) -> list[Any]:
    return [b for b in result.blocks if b.block_type == block_type]


def _warning_codes(result) -> list[str]:
    return [w.code for w in result.warnings]


def _notice(result, code: str) -> int:
    return sum(1 for w in result.warnings if w.code == code)


# ---------------------------------------------------------------------------
# A. Provenance（§7.5 / §12 #21/#23-#26）
# ---------------------------------------------------------------------------


def test_safe_inline_image_becomes_typed_standalone_image() -> None:
    result = _parse('![Alt text](https://example.com/a.png "The Title")')

    images = _blocks_by_type(result, "image")
    assert len(images) == 1
    assert images[0].text_content is None
    assert _image_payload(images[0]) == {
        "source_url": "https://example.com/a.png",
        "alt_text": "Alt text",
        "title": "The Title",
        "position_kind": "standalone",
    }
    # 纯图片段落不产生空 paragraph（§5.2）。
    assert _blocks_by_type(result, "paragraph") == []
    assert result.outcome == "stable_document_ready"


def test_unsafe_scheme_inline_image_still_typed_with_verbatim_source_url() -> None:
    result = _parse("![a](javascript:alert(1))")

    images = _blocks_by_type(result, "image")
    assert len(images) == 1
    assert _image_payload(images[0])["source_url"] == "javascript:alert(1)"
    assert _image_payload(images[0])["alt_text"] == "a"
    # 图片不再是 stripped link；unsafe_link_protocol 是链接专用 warning。
    assert "unsafe_link_protocol" not in _warning_codes(result)
    assert _blocks_by_type(result, "paragraph") == []


def test_unsafe_scheme_image_with_title_keeps_title() -> None:
    result = _parse('![a](javascript:alert(1) "T")')

    images = _blocks_by_type(result, "image")
    assert len(images) == 1
    assert _image_payload(images[0])["source_url"] == "javascript:alert(1)"
    assert _image_payload(images[0])["title"] == "T"


def test_unsafe_scheme_image_in_angle_brackets_still_typed() -> None:
    result = _parse("![a](<javascript:alert(1)>)")

    images = _blocks_by_type(result, "image")
    assert len(images) == 1
    assert _image_payload(images[0])["source_url"] == "javascript:alert(1)"


def test_data_uri_png_typed_and_svg_typed() -> None:
    result = _parse("![a](data:image/png;base64,x)\n\n![b](data:image/svg+xml;base64,y)")

    images = _blocks_by_type(result, "image")
    assert len(images) == 2
    assert _image_payload(images[0])["source_url"] == "data:image/png;base64,x"
    assert _image_payload(images[1])["source_url"] == "data:image/svg+xml;base64,y"


def test_raw_backslash_source_url_distinct_from_percent_encoded() -> None:
    raw = _parse(f"![a](http://example.com/a{_BS}b.png)")
    encoded = _parse("![a](http://example.com/a%5Cb.png)")

    raw_url = _image_payload(_blocks_by_type(raw, "image")[0])["source_url"]
    encoded_url = _image_payload(_blocks_by_type(encoded, "image")[0])["source_url"]
    assert raw_url == f"http://example.com/a{_BS}b.png"
    assert encoded_url == "http://example.com/a%5Cb.png"
    assert raw_url != encoded_url


def test_raw_space_angle_destination_distinct_from_percent_encoded() -> None:
    raw = _parse("![a](<http://example.com/a b.png>)")
    encoded = _parse("![a](http://example.com/a%20b.png)")

    raw_url = _image_payload(_blocks_by_type(raw, "image")[0])["source_url"]
    encoded_url = _image_payload(_blocks_by_type(encoded, "image")[0])["source_url"]
    assert raw_url == "http://example.com/a b.png"
    assert encoded_url == "http://example.com/a%20b.png"
    assert raw_url != encoded_url


def test_source_url_preserves_scheme_and_host_case() -> None:
    result = _parse("![a](HTTP://Example.COM/a.png)")

    payload = _image_payload(_blocks_by_type(result, "image")[0])
    assert payload["source_url"] == "HTTP://Example.COM/a.png"


def test_empty_destination_source_url_is_empty_string() -> None:
    bare = _parse("![a]()")
    angle = _parse("![a](<>)")

    assert _image_payload(_blocks_by_type(bare, "image")[0])["source_url"] == ""
    assert _image_payload(_blocks_by_type(angle, "image")[0])["source_url"] == ""
    assert _image_payload(_blocks_by_type(bare, "image")[0])["alt_text"] == "a"


def test_angle_destination_padding_preserved_in_source_url() -> None:
    result = _parse("![a](< https://example.com/a.png >)")

    payload = _image_payload(_blocks_by_type(result, "image")[0])
    assert payload["source_url"] == " https://example.com/a.png "


def test_escaped_punctuation_and_entity_decoded_in_source_url() -> None:
    escaped = _parse(f"![a](http://example.com/a{_BS}(b{_BS}).png)")
    entity = _parse("![a](https://example.com/a&amp;b.png)")
    at_sign = _parse(f"![a](http://example.com/a{_BS}@evil.com/a.png)")

    assert (
        _image_payload(_blocks_by_type(escaped, "image")[0])["source_url"]
        == "http://example.com/a(b).png"
    )
    assert (
        _image_payload(_blocks_by_type(entity, "image")[0])["source_url"]
        == "https://example.com/a&b.png"
    )
    assert (
        _image_payload(_blocks_by_type(at_sign, "image")[0])["source_url"]
        == "http://example.com/a@evil.com/a.png"
    )


def test_invalid_syntax_images_stay_plain_text() -> None:
    # bare-space destination（§7.5.3：grammar invalid）
    bare_space = _parse("![a](http://exa mple.com/a.png)")
    assert _blocks_by_type(bare_space, "image") == []
    # 控制字符 destination
    control = _parse("![a](https://example.com/a\x01.png)")
    assert _blocks_by_type(control, "image") == []
    # 未定义 reference
    undefined_ref = _parse("![a][missing]")
    assert _blocks_by_type(undefined_ref, "image") == []


def test_reference_image_source_url_uses_definition_semantic_destination() -> None:
    result = _parse(f"![a][ref]\n\n[ref]: http://example.com/r{_BS}b.png")

    images = _blocks_by_type(result, "image")
    assert len(images) == 1
    assert _image_payload(images[0])["source_url"] == f"http://example.com/r{_BS}b.png"


def test_reference_image_raw_space_and_case_preserved() -> None:
    spaced = _parse("![a][ref]\n\n[ref]: <http://example.com/r s.png>")
    upper = _parse("![a][ref]\n\n[ref]: HTTP://Example.COM/r.png")

    assert (
        _image_payload(_blocks_by_type(spaced, "image")[0])["source_url"]
        == "http://example.com/r s.png"
    )
    assert (
        _image_payload(_blocks_by_type(upper, "image")[0])["source_url"]
        == "HTTP://Example.COM/r.png"
    )


def test_reference_image_title_from_definition() -> None:
    result = _parse('![a][ref]\n\n[ref]: https://example.com/r.png "RT"')

    payload = _image_payload(_blocks_by_type(result, "image")[0])
    assert payload["title"] == "RT"


def test_unsafe_reference_image_typed_and_definition_line_stays_visible() -> None:
    result = _parse("![a][ref]\n\n[ref]: javascript:alert(1)")

    images = _blocks_by_type(result, "image")
    assert len(images) == 1
    assert _image_payload(images[0])["source_url"] == "javascript:alert(1)"
    # 定义行保持可见 paragraph（ordinary link 零回归约束）。
    paragraphs = _blocks_by_type(result, "paragraph")
    assert [p.text_content for p in paragraphs] == ["[ref]: javascript:alert(1)"]


def test_unsafe_reference_image_with_title() -> None:
    result = _parse('![a][ref]\n\n[ref]: javascript:alert(1) "UT"')

    images = _blocks_by_type(result, "image")
    assert len(images) == 1
    assert _image_payload(images[0])["source_url"] == "javascript:alert(1)"
    assert _image_payload(images[0])["title"] == "UT"


@pytest.mark.parametrize(
    ("definition", "expected_title"),
    [
        ('[ref]:\n  javascript:alert(1) "UT"', "UT"),
        ('[ref]: javascript:alert(1)\n  "UT"', "UT"),
    ],
)
def test_unsafe_multiline_reference_image_keeps_provenance(
    definition: str,
    expected_title: str,
) -> None:
    result = _parse(f"![danger][ref]\n\n{definition}")

    images = _blocks_by_type(result, "image")
    assert len(images) == 1
    payload = _image_payload(images[0])
    assert payload["source_url"] == "javascript:alert(1)"
    assert payload["title"] == expected_title
    assert any(
        "[ref]:" in (block.text_content or "") for block in _blocks_by_type(result, "paragraph")
    )


def test_unsafe_multiline_reference_does_not_activate_ordinary_link() -> None:
    result = _parse("[x][ref]\n\n[ref]:\n  javascript:alert(1)")

    paragraphs = _blocks_by_type(result, "paragraph")
    assert paragraphs[0].text_content == "[x][ref]"
    assert paragraphs[0].payload_json == {}
    assert "[ref]:" in (paragraphs[1].text_content or "")


def test_reference_collapsed_shortcut_and_case_fold_forms() -> None:
    collapsed = _parse("![a][]\n\n[a]: https://example.com/c.png")
    shortcut = _parse("![a]\n\n[a]: https://example.com/s.png")
    case_fold = _parse("![a][REF]\n\n[ref]: https://example.com/r.png")

    for result, expected in (
        (collapsed, "https://example.com/c.png"),
        (shortcut, "https://example.com/s.png"),
        (case_fold, "https://example.com/r.png"),
    ):
        images = _blocks_by_type(result, "image")
        assert len(images) == 1
        assert _image_payload(images[0])["source_url"] == expected


def test_reference_forward_definition_supported() -> None:
    # 定义在使用之后（block pass 先于 inline pass）。
    result = _parse("![a][ref]\n\n[ref]: https://example.com/fwd.png")

    images = _blocks_by_type(result, "image")
    assert len(images) == 1
    assert _image_payload(images[0])["source_url"] == "https://example.com/fwd.png"


def test_reference_duplicate_definitions_first_wins() -> None:
    result = _parse(
        "![a][ref]\n\n[ref]: https://example.com/first.png\n\n[ref]: https://example.com/second.png"
    )

    images = _blocks_by_type(result, "image")
    assert len(images) == 1
    assert _image_payload(images[0])["source_url"] == "https://example.com/first.png"


def test_link_and_image_sharing_same_reference() -> None:
    result = _parse("text [x][ref] and ![a][ref] end\n\n[ref]: https://example.com/r.png")

    paragraphs = _blocks_by_type(result, "paragraph")
    assert len(paragraphs) == 1
    payload = paragraphs[0].payload_json
    assert payload["links"] == [{"text": "x", "href": "https://example.com/r.png"}]
    assert payload["inline_marks"] == [
        {"type": "link", "start": 5, "end": 6, "href": "https://example.com/r.png"}
    ]
    inline_images = payload["inline_images"]
    assert len(inline_images) == 1
    assert inline_images[0]["source_url"] == "https://example.com/r.png"


def test_attrs_src_not_used_as_source_url() -> None:
    # raw backslash 下 token.attrs.src 是 %5C 规范化值；source_url 必须保留
    # U+005C（§12 #25：attrs.src 不得作为 source_url 唯一来源）。
    result = _parse(f"![a](http://example.com/a{_BS}b.png)")

    images = _blocks_by_type(result, "image")
    source_url = _image_payload(images[0])["source_url"]
    assert source_url == f"http://example.com/a{_BS}b.png"
    assert "%5C" not in source_url


# A2. provenance invariant fail-closed（§7.5.1 三层语义：meta 缺失/非 str
# 是 seam 不变量违反，必须固定失败而非静默返回 ""）。


def _raw_image_token(meta: Any) -> Token:
    token = Token(type="image", tag="img", nesting=0)
    token.meta = meta
    return token


def test_semantic_destination_missing_meta_key_fails_closed() -> None:
    token = _raw_image_token({})

    with pytest.raises(MarkdownSourceParseError):
        _image_semantic_destination(token)


def test_semantic_destination_none_meta_fails_closed() -> None:
    token = _raw_image_token(None)

    with pytest.raises(MarkdownSourceParseError):
        _image_semantic_destination(token)


def test_semantic_destination_non_str_meta_fails_closed_without_echo() -> None:
    token = _raw_image_token({"semantic_destination": b"javascript:evil(x)"})

    with pytest.raises(MarkdownSourceParseError) as exc_info:
        _image_semantic_destination(token)
    # 异常消息只描述 invariant 违反事实，不回显 Markdown / URL 内容。
    message = str(exc_info.value)
    assert "javascript" not in message
    assert "evil" not in message
    assert "b'" not in message


def test_semantic_destination_empty_string_is_legal() -> None:
    token = _raw_image_token({"semantic_destination": ""})

    assert _image_semantic_destination(token) == ""


# ---------------------------------------------------------------------------
# B. Representation（§5/§6/§6.5 / §12 #1-#5b/#15-#18）
# ---------------------------------------------------------------------------


def test_standalone_image_block_coordinates() -> None:
    result = _parse("![a](https://example.com/a.png)\n\nBody paragraph.")

    images = _blocks_by_type(result, "image")
    assert len(images) == 1
    assert images[0].block_id == "b1"
    assert images[0].parent_block_id is None
    assert images[0].order_index == 0
    assert images[0].source_range.line_start == 1
    paragraphs = _blocks_by_type(result, "paragraph")
    assert [p.text_content for p in paragraphs] == ["Body paragraph."]
    assert paragraphs[0].order_index == 1


def test_multiple_images_paragraph_all_standalone_in_token_order() -> None:
    result = _parse("![a](https://example.com/1.png) ![b](https://example.com/2.png)")

    images = _blocks_by_type(result, "image")
    assert len(images) == 2
    assert [i.payload_json["source_url"] for i in images] == [
        "https://example.com/1.png",
        "https://example.com/2.png",
    ]
    assert [i.order_index for i in images] == [0, 1]
    assert _blocks_by_type(result, "paragraph") == []


def test_empty_alt_standalone_image() -> None:
    result = _parse("![](https://example.com/a.png)")

    images = _blocks_by_type(result, "image")
    assert len(images) == 1
    assert _image_payload(images[0])["alt_text"] == ""


@pytest.mark.parametrize(
    ("source", "expected_text", "expected_before"),
    [
        ("hello![a](u)world", "hello world", [5]),
        ("hello ![a](u) world", "hello  world", [6]),
        ("hello![a](u1)![b](u2)world", "hello world", [5, 5]),
        ("![a](u)hello", "hello", [0]),
        ("hello![a](u)", "hello", [5]),
        ("👍![a](u)中", "👍 中", [2]),
    ],
)
def test_inline_image_separator_space_and_offsets(
    source: str, expected_text: str, expected_before: list[int]
) -> None:
    result = _parse(source)

    paragraphs = _blocks_by_type(result, "paragraph")
    assert len(paragraphs) == 1
    assert paragraphs[0].text_content == expected_text
    inline_images = paragraphs[0].payload_json["inline_images"]
    assert [img["before_utf16"] for img in inline_images] == expected_before
    for img in inline_images:
        assert set(img) == {"source_url", "alt_text", "title", "before_utf16"}
        assert img["title"] is None


def test_style_wrapped_image_is_standalone_without_marks() -> None:
    for source in ("**![i](u)**", "*![i](u)*", "~~![i](u)~~"):
        result = _parse(source)
        assert len(_blocks_by_type(result, "image")) == 1, source
        assert _blocks_by_type(result, "paragraph") == [], source
        assert "inline_marks" not in _blocks_by_type(result, "image")[0].payload_json


def test_link_wrapped_image_promotes_standalone_with_notice() -> None:
    result = _parse("[![i](u)](https://example.com)")

    images = _blocks_by_type(result, "image")
    assert len(images) == 1
    assert _image_payload(images[0])["source_url"] == "u"
    assert _blocks_by_type(result, "paragraph") == []
    assert _notice(result, "image_link_wrapper_removed") == 1
    notice = next(w for w in result.warnings if w.code == "image_link_wrapper_removed")
    assert notice.classification == "adaptation_notice"
    assert notice.blocks_freeze is False


def test_link_wrapped_mixed_content_keeps_link_mark_and_notice() -> None:
    result = _parse("[text ![i](u)](https://example.com)")

    paragraphs = _blocks_by_type(result, "paragraph")
    assert len(paragraphs) == 1
    payload = paragraphs[0].payload_json
    assert payload["inline_marks"] == [
        {"type": "link", "start": 0, "end": 4, "href": "https://example.com"}
    ]
    assert payload["links"] == [{"text": "text", "href": "https://example.com"}]
    assert len(payload["inline_images"]) == 1
    assert _notice(result, "image_link_wrapper_removed") == 1


def test_style_wrapped_image_between_text_no_empty_mark() -> None:
    result = _parse("before **![i](u)** after")

    paragraphs = _blocks_by_type(result, "paragraph")
    assert len(paragraphs) == 1
    assert paragraphs[0].text_content == "before  after"
    payload = paragraphs[0].payload_json
    assert "inline_marks" not in payload
    assert payload["inline_images"][0]["before_utf16"] == 7


def test_alt_and_url_never_enter_paragraph_text() -> None:
    result = _parse("hello ![Alt](https://example.com/a.png) world")

    paragraphs = _blocks_by_type(result, "paragraph")
    assert paragraphs[0].text_content == "hello  world"
    assert "Alt" not in paragraphs[0].text_content
    assert "example.com" not in paragraphs[0].text_content


def test_image_only_heading_promotes_standalone_image() -> None:
    result = _parse("# ![](https://example.com/h.png)")

    assert _blocks_by_type(result, "heading") == []
    images = _blocks_by_type(result, "image")
    assert len(images) == 1
    assert images[0].parent_block_id is None
    assert images[0].source_range.line_start == 1
    assert _notice(result, "image_only_in_narrative_container") == 1
    notice = next(w for w in result.warnings if w.code == "image_only_in_narrative_container")
    assert notice.classification == "adaptation_notice"
    assert notice.blocks_freeze is False


def test_mixed_heading_keeps_heading_with_inline_images() -> None:
    result = _parse("# Intro ![](https://example.com/h.png)")

    headings = _blocks_by_type(result, "heading")
    assert len(headings) == 1
    assert headings[0].text_content == "Intro "
    inline_images = headings[0].payload_json["inline_images"]
    assert inline_images[0]["before_utf16"] == 6
    assert _notice(result, "image_only_in_narrative_container") == 0


def test_image_only_blockquote_promotes_standalone_image() -> None:
    result = _parse("> ![](https://example.com/q.png)")

    assert _blocks_by_type(result, "blockquote") == []
    images = _blocks_by_type(result, "image")
    assert len(images) == 1
    assert _notice(result, "image_only_in_narrative_container") == 1


def test_mixed_blockquote_keeps_blockquote_with_inline_images() -> None:
    result = _parse("> quoted text ![a](u)")

    blockquotes = _blocks_by_type(result, "blockquote")
    assert len(blockquotes) == 1
    assert blockquotes[0].text_content == "quoted text "
    inline_images = blockquotes[0].payload_json["inline_images"]
    assert inline_images[0]["before_utf16"] == 12


def test_image_only_list_item_keeps_wrapper_and_promotes_image() -> None:
    result = _parse("- ![](https://example.com/li.png)")

    lists = _blocks_by_type(result, "list")
    assert len(lists) == 1
    assert _blocks_by_type(result, "list_item") == []
    images = _blocks_by_type(result, "image")
    assert len(images) == 1
    assert images[0].parent_block_id == lists[0].block_id
    assert _notice(result, "image_only_in_narrative_container") == 1


def test_mixed_list_item_keeps_item_with_inline_images() -> None:
    result = _parse("- item text ![a](u)")

    items = _blocks_by_type(result, "list_item")
    assert len(items) == 1
    assert items[0].text_content == "item text "
    assert items[0].payload_json["inline_images"][0]["before_utf16"] == 10
    assert _notice(result, "image_only_in_narrative_container") == 0


def test_mixed_list_with_text_and_image_items() -> None:
    result = _parse("- first item\n- ![](u)")

    items = _blocks_by_type(result, "list_item")
    images = _blocks_by_type(result, "image")
    assert [i.text_content for i in items] == ["first item"]
    assert len(images) == 1
    assert images[0].parent_block_id == _blocks_by_type(result, "list")[0].block_id


# B2. 嵌套 list 中的空 list_item 死路（合同 §6.5.8 list_item 规则）。
_NESTED_IMAGE_ITEM_MD = (
    f"{_ENGLISH_PARAGRAPH}\n\n"
    "- ![](https://example.com/li.png)\n"
    "  - nested child one\n"
    "  - nested child two\n"
)


def test_image_only_item_with_nested_list_emits_no_empty_list_item() -> None:
    result = _parse(_NESTED_IMAGE_ITEM_MD)

    # 空外层 list_item 不输出（§6.5.8：空 list_item 不输出）。
    for block in result.blocks:
        assert block.text_content != "", f"empty {block.block_type} block emitted"


def test_image_only_item_with_nested_list_reparents_to_outer_wrapper() -> None:
    result = _parse(_NESTED_IMAGE_ITEM_MD)

    lists = _blocks_by_type(result, "list")
    assert len(lists) == 2
    outer, nested = lists
    images = _blocks_by_type(result, "image")
    assert len(images) == 1
    # promoted image 的 parent 指向外层 list wrapper（§6.5.8）。
    assert images[0].parent_block_id == outer.block_id
    # 嵌套 list wrapper 改挂外层 list wrapper，子内容不丢失。
    assert nested.parent_block_id == outer.block_id
    items = _blocks_by_type(result, "list_item")
    assert [i.text_content for i in items] == [
        "nested child one",
        "nested child two",
    ]
    assert all(i.parent_block_id == nested.block_id for i in items)
    # 图片占原 item 源序，嵌套结构紧随其后（确定性落位）。
    assert images[0].order_index < nested.order_index
    assert _notice(result, "image_only_in_narrative_container") == 1


def test_image_only_item_with_nested_list_normalizes_without_error() -> None:
    normalized = normalize_input_document(
        InputSuitabilityRequest(
            source_type="markdown_file",
            filename="notes.md",
            text=_NESTED_IMAGE_ITEM_MD,
        )
    )

    for block in normalized.blocks:
        if block.block_type == "list_item":
            assert block.text_content


def test_promoted_ordered_item_consumes_its_ordinal() -> None:
    result = _parse("3. ![a](u)\n4. next outer item")

    items = _blocks_by_type(result, "list_item")
    assert len(items) == 1
    assert items[0].text_content == "next outer item"
    assert items[0].payload_json["ordinal"] == 4
    assert items[0].payload_json["marker"] == "4."


def test_image_only_item_with_continuation_never_emits_empty_item() -> None:
    source = f"{_ENGLISH_PARAGRAPH}\n\n- ![a](https://example.com/a.png)\n\n  continuation text"
    result = _parse(source)

    lists = _blocks_by_type(result, "list")
    assert len(lists) == 1
    wrapper = lists[0]
    images = _blocks_by_type(result, "image")
    assert len(images) == 1
    assert images[0].parent_block_id == wrapper.block_id
    continuation = next(
        block
        for block in _blocks_by_type(result, "paragraph")
        if block.text_content == "continuation text"
    )
    assert continuation.parent_block_id == wrapper.block_id
    assert _blocks_by_type(result, "list_item") == []

    normalized = normalize_input_document(
        InputSuitabilityRequest(
            source_type="markdown_file",
            filename="notes.md",
            text=source,
        )
    )
    assert all(block.text_content for block in normalized.blocks if block.block_type == "list_item")


def test_plain_nested_text_list_structure_unchanged() -> None:
    # 守卫：普通嵌套文字列表结构完全不变（item 保留，嵌套 list 仍挂 item）。
    result = _parse("- parent text\n  - child one\n  - child two")

    items = _blocks_by_type(result, "list_item")
    assert [i.text_content for i in items] == [
        "parent text",
        "child one",
        "child two",
    ]
    lists = _blocks_by_type(result, "list")
    assert len(lists) == 2
    assert items[0].parent_block_id == lists[0].block_id
    assert lists[1].parent_block_id == items[0].block_id
    assert items[1].parent_block_id == lists[1].block_id
    assert items[2].parent_block_id == lists[1].block_id
    assert _notice(result, "image_only_in_narrative_container") == 0


# B3. 固定图片 payload schema（合同 §7.1/§7.2）：title 键无条件存在，
# 无 title 时为 None，显式空 title 保留 ""。
_STANDALONE_FIELD_SET = {"source_url", "alt_text", "title", "position_kind"}
_INLINE_FIELD_SET = {"source_url", "alt_text", "title", "before_utf16"}


def test_standalone_payload_schema_title_absent_is_none() -> None:
    result = _parse("![a](https://example.com/a.png)")

    payload = _image_payload(_blocks_by_type(result, "image")[0])
    assert set(payload) == _STANDALONE_FIELD_SET
    assert payload["title"] is None


def test_standalone_payload_schema_explicit_empty_title() -> None:
    result = _parse('![a](https://example.com/a.png "")')

    payload = _image_payload(_blocks_by_type(result, "image")[0])
    assert set(payload) == _STANDALONE_FIELD_SET
    assert payload["title"] == ""


def test_standalone_payload_schema_non_empty_title() -> None:
    result = _parse('![a](https://example.com/a.png "T")')

    payload = _image_payload(_blocks_by_type(result, "image")[0])
    assert set(payload) == _STANDALONE_FIELD_SET
    assert payload["title"] == "T"


def test_inline_payload_schema_title_absent_and_empty() -> None:
    absent = _parse("hello ![a](u) world")
    empty = _parse('hello ![a](u "") world')

    for result, expected_title in ((absent, None), (empty, "")):
        paragraphs = _blocks_by_type(result, "paragraph")
        assert len(paragraphs) == 1
        entry = paragraphs[0].payload_json["inline_images"][0]
        assert set(entry) == _INLINE_FIELD_SET
        assert entry["title"] == expected_title


def test_mixed_list_item_inline_entry_schema() -> None:
    result = _parse("- item text ![a](u)")

    entry = _blocks_by_type(result, "list_item")[0].payload_json["inline_images"][0]
    assert set(entry) == _INLINE_FIELD_SET
    assert entry["title"] is None


def test_unsafe_inline_image_payload_schema_titles() -> None:
    absent = _parse("![a](javascript:alert(1))")
    empty = _parse('![a](javascript:alert(1) "")')
    present = _parse('![a](javascript:alert(1) "T")')

    for result, expected_title in (
        (absent, None),
        (empty, ""),
        (present, "T"),
    ):
        payload = _image_payload(_blocks_by_type(result, "image")[0])
        assert set(payload) == _STANDALONE_FIELD_SET
        assert payload["title"] == expected_title


def test_safe_reference_image_payload_schema_titles() -> None:
    absent = _parse("![a][ref]\n\n[ref]: https://example.com/r.png")
    empty = _parse('![a][ref]\n\n[ref]: https://example.com/r.png ""')
    present = _parse('![a][ref]\n\n[ref]: https://example.com/r.png "RT"')

    for result, expected_title in (
        (absent, None),
        (empty, ""),
        (present, "RT"),
    ):
        payload = _image_payload(_blocks_by_type(result, "image")[0])
        assert set(payload) == _STANDALONE_FIELD_SET
        assert payload["title"] == expected_title


def test_unsafe_reference_image_payload_schema_titles() -> None:
    absent = _parse("![a][ref]\n\n[ref]: javascript:alert(1)")
    empty = _parse('![a][ref]\n\n[ref]: javascript:alert(1) ""')
    present = _parse('![a][ref]\n\n[ref]: javascript:alert(1) "UT"')

    for result, expected_title in (
        (absent, None),
        (empty, ""),
        (present, "UT"),
    ):
        payload = _image_payload(_blocks_by_type(result, "image")[0])
        assert set(payload) == _STANDALONE_FIELD_SET
        assert payload["title"] == expected_title


_IMAGE_TABLE_MD = """| A | B |
| --- | --- |
| text | ![cell img](https://example.com/c.png) |
"""


def test_image_only_table_cell_keeps_cell_with_metadata_only_policy() -> None:
    result = _parse(_IMAGE_TABLE_MD)

    cells = _blocks_by_type(result, "table_cell")
    # 表头两格 + body 两格。
    assert len(cells) == 4
    image_cell = cells[3]
    assert image_cell.text_content is None
    payload = image_cell.payload_json
    assert payload["column_index"] == 1
    assert payload["is_header"] is False
    assert payload["inline_images"] == [
        {
            "source_url": "https://example.com/c.png",
            "alt_text": "cell img",
            "title": None,
            "before_utf16": 0,
        }
    ]
    assert image_cell.interpretation_policy == dict(_TABLE_CELL_METADATA_ONLY_POLICY)
    # 不产生 narrative notice，不提升 sibling image。
    assert _notice(result, "image_only_in_narrative_container") == 0
    assert _blocks_by_type(result, "image") == []


def test_mixed_table_cell_keeps_default_policy_and_text() -> None:
    result = _parse("| text ![a](u) | ![b](v) |\n| --- | --- |\n| c | d |")

    cells = _blocks_by_type(result, "table_cell")
    mixed_cell = cells[0]
    assert mixed_cell.text_content == "text "
    assert mixed_cell.interpretation_policy is None
    assert mixed_cell.payload_json["inline_images"][0]["before_utf16"] == 5
    image_only_cell = cells[1]
    assert image_only_cell.text_content is None
    assert image_only_cell.interpretation_policy == dict(_TABLE_CELL_METADATA_ONLY_POLICY)


def test_table_header_cell_image_only_policy() -> None:
    result = _parse("| ![](u) | B |\n| --- | --- |\n| c | d |")

    cells = _blocks_by_type(result, "table_cell")
    header_image_cell = cells[0]
    assert header_image_cell.payload_json["is_header"] is True
    assert header_image_cell.text_content is None
    assert header_image_cell.interpretation_policy == dict(_TABLE_CELL_METADATA_ONLY_POLICY)


def test_block_id_and_order_deterministic_on_fresh_reparse() -> None:
    source = "# ![](u)\n\ntext ![a](v)\n\n- ![](w)\n\n> ![](x)"
    first = _parse(source)
    second = _parse(source)

    def _project(result):
        return [(b.block_id, b.block_type, b.parent_block_id, b.order_index) for b in result.blocks]

    assert _project(first) == _project(second)
    assert [b.block_id for b in first.blocks] == [f"b{i + 1}" for i in range(len(first.blocks))]


# ---------------------------------------------------------------------------
# C. Carrier 链（§14 C-40 / §12 #16 parser 侧）
# ---------------------------------------------------------------------------


def _gate_ready_markdown() -> str:
    return (
        "## Reading Notes\n\n"
        f"{_ENGLISH_PARAGRAPH}\n\n"
        "The table below carries one image-only cell.\n\n"
        "| A | B |\n"
        "| --- | --- |\n"
        "| text | ![cell](https://example.com/c.png) |\n"
    )


def test_parsed_block_policy_only_for_image_only_table_cell() -> None:
    result = _parse(_gate_ready_markdown())

    for block in result.blocks:
        if block.block_type == "table_cell" and block.text_content is None:
            assert block.interpretation_policy == dict(_TABLE_CELL_METADATA_ONLY_POLICY)
        else:
            assert block.interpretation_policy is None


def test_callout_icon_promotion_preserves_image_only_table_cell_policy() -> None:
    result = _parse(
        "<aside>\n🎯\n\nCallout body.\n</aside>\n\n"
        "| image | text |\n| --- | --- |\n| ![a](u) | ordinary cell |"
    )

    wrapper = next(
        block
        for block in result.blocks
        if block.payload_json.get("source_semantic_hint") == "html_aside"
    )
    assert wrapper.payload_json["display_icon"] == "🎯"
    image_only_cell = next(
        block
        for block in result.blocks
        if block.block_type == "table_cell" and block.text_content is None
    )
    assert image_only_cell.interpretation_policy == dict(_TABLE_CELL_METADATA_ONLY_POLICY)


def test_normalizer_carries_explicit_policy_to_stable_document_block() -> None:
    normalized = normalize_input_document(
        InputSuitabilityRequest(
            source_type="markdown_file",
            filename="notes.md",
            text=_gate_ready_markdown(),
        )
    )

    image_cells = [
        b for b in normalized.blocks if b.block_type == "table_cell" and b.text_content is None
    ]
    assert len(image_cells) == 1
    policy = image_cells[0].interpretation_policy
    assert policy is not None
    assert policy.default_route == "metadata_only"
    assert policy.rag_eligible is False
    assert policy.allowed_source_scope == ["table_cell"]

    # 普通正文 block 应用默认 main_reading policy。
    paragraph = next(b for b in normalized.blocks if b.block_type == "paragraph")
    assert paragraph.interpretation_policy is not None
    assert paragraph.interpretation_policy.default_route == "main_reading"


# ---------------------------------------------------------------------------
# D. Routing（O-1 / §12 #6/#7 gate 侧）
# ---------------------------------------------------------------------------


def test_image_with_enough_text_is_stable_document_ready() -> None:
    result = evaluate_input_suitability(
        InputSuitabilityRequest(
            source_type="markdown_file",
            filename="report.md",
            text=(f"{_ENGLISH_PARAGRAPH}\n\n![Map of the site](https://example.com/site-map.png)"),
        )
    )

    assert result.outcome == "stable_document_ready"
    assert "image_ocr_uncertain" not in result.flags
    assert "markdown_complex_structure" not in result.flags
    assert all(record.code != "image_ocr_uncertain" for record in result.adaptations)


def test_image_only_document_rejected_by_text_eligibility_not_image_flag() -> None:
    result = evaluate_input_suitability(
        InputSuitabilityRequest(
            source_type="markdown_file",
            filename="report.md",
            text="![only image](https://example.com/a.png)",
        )
    )

    assert result.outcome == "input_rejected_or_action_required"
    assert "image_ocr_uncertain" not in result.flags
    assert "too_short_for_learning" in result.flags


def test_gate_module_has_no_image_regex_route() -> None:
    # §12 #28 架构守卫：删除 has_image 路由及其 dead regex/field。
    import app.services.reader_orchestration.input_suitability_gate as gate_module

    assert not hasattr(gate_module, "_MARKDOWN_IMAGE_PATTERN")
    complexity_fields = gate_module._MarkdownComplexity.__dataclass_fields__
    assert "has_image" not in complexity_fields


# ---------------------------------------------------------------------------
# E. Ordinary link 基线零回归（§7.5.6 / §12 #22）
# ---------------------------------------------------------------------------


def _link_baseline_cases() -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (
            "safe_link",
            "Intro paragraph with a [safe link](https://example.com/a.png) "
            "and plain text here for reading.",
            {
                "outcome": "stable_document_ready",
                "blocks": [
                    (
                        "paragraph",
                        "Intro paragraph with a safe link and plain text here for reading.",
                        {
                            "links": [
                                {
                                    "text": "safe link",
                                    "href": "https://example.com/a.png",
                                }
                            ],
                            "inline_marks": [
                                {
                                    "type": "link",
                                    "start": 23,
                                    "end": 32,
                                    "href": "https://example.com/a.png",
                                }
                            ],
                        },
                    )
                ],
                "warning_codes": [],
            },
        ),
        (
            "unsafe_link",
            "Intro paragraph with an [unsafe link](javascript:alert(1)) "
            "and plain text here for reading.",
            {
                "outcome": "stable_document_ready",
                "blocks": [
                    (
                        "paragraph",
                        "Intro paragraph with an unsafe link and plain text here for reading.",
                        {
                            "links": [],
                            "stripped_links": [
                                {
                                    "text": "unsafe link",
                                    "href": "javascript:alert(1)",
                                    "reason": "unsafe_protocol",
                                }
                            ],
                        },
                    )
                ],
                "warning_codes": ["unsafe_link_protocol"],
            },
        ),
        (
            "backslash_link",
            f"Intro paragraph with a [backslash link](http://example.com/a{_BS}b.png) "
            "and plain text here.",
            {
                "outcome": "stable_document_ready",
                "blocks": [
                    (
                        "paragraph",
                        "Intro paragraph with a backslash link and plain text here.",
                        {
                            "links": [
                                {
                                    "text": "backslash link",
                                    "href": "http://example.com/a%5Cb.png",
                                }
                            ],
                            "inline_marks": [
                                {
                                    "type": "link",
                                    "start": 23,
                                    "end": 37,
                                    "href": "http://example.com/a%5Cb.png",
                                }
                            ],
                        },
                    )
                ],
                "warning_codes": [],
            },
        ),
        (
            "emphasis_in_link",
            "Intro with [emphasis **inside** link](https://example.com/x) "
            "for the committee to review today.",
            {
                "outcome": "stable_document_ready",
                "blocks": [
                    (
                        "paragraph",
                        "Intro with emphasis inside link for the committee to review today.",
                        {
                            "links": [
                                {
                                    "text": "emphasis inside link",
                                    "href": "https://example.com/x",
                                }
                            ],
                            "inline_marks": [
                                {"type": "strong", "start": 20, "end": 26},
                                {
                                    "type": "link",
                                    "start": 11,
                                    "end": 31,
                                    "href": "https://example.com/x",
                                },
                            ],
                        },
                    )
                ],
                "warning_codes": [],
            },
        ),
        (
            "plain_paragraph",
            "Committee reviewed the proposal and agreed that the appendix "
            "remains available before the vote.",
            {
                "outcome": "stable_document_ready",
                "blocks": [
                    (
                        "paragraph",
                        "Committee reviewed the proposal and agreed that the "
                        "appendix remains available before the vote.",
                        {},
                    )
                ],
                "warning_codes": [],
            },
        ),
        (
            "reference_link_safe_definition",
            "[x][ref] reference link usage followed by plain text words for "
            "the ratio check to pass easily.\n\n[ref]: https://example.com/r.png",
            {
                "outcome": "stable_document_ready",
                "blocks": [
                    (
                        "paragraph",
                        "x reference link usage followed by plain text words "
                        "for the ratio check to pass easily.",
                        {
                            "links": [{"text": "x", "href": "https://example.com/r.png"}],
                            "inline_marks": [
                                {
                                    "type": "link",
                                    "start": 0,
                                    "end": 1,
                                    "href": "https://example.com/r.png",
                                }
                            ],
                        },
                    )
                ],
                "warning_codes": [],
            },
        ),
        (
            "reference_link_unsafe_definition",
            "[x][ref] reference link usage followed by plain text words for "
            "the ratio check to pass easily.\n\n[ref]: javascript:alert(1)",
            {
                "outcome": "stable_document_ready",
                "blocks": [
                    (
                        "paragraph",
                        "[x][ref] reference link usage followed by plain text "
                        "words for the ratio check to pass easily.",
                        {},
                    ),
                    ("paragraph", "[ref]: javascript:alert(1)", {}),
                ],
                "warning_codes": [],
            },
        ),
        (
            "angle_space_link",
            "Text with raw angle link [x](<http://example.com/a b.png>) "
            "inside a longer English paragraph body.",
            {
                "outcome": "stable_document_ready",
                "blocks": [
                    (
                        "paragraph",
                        "Text with raw angle link x inside a longer English paragraph body.",
                        {
                            "links": [
                                {
                                    "text": "x",
                                    "href": "http://example.com/a%20b.png",
                                }
                            ],
                            "inline_marks": [
                                {
                                    "type": "link",
                                    "start": 25,
                                    "end": 26,
                                    "href": "http://example.com/a%20b.png",
                                }
                            ],
                        },
                    )
                ],
                "warning_codes": [],
            },
        ),
        (
            "mixed_safe_unsafe_links",
            "A [safe](https://example.com/s) and an unsafe "
            "[bad](javascript:alert(1)) mixed into one paragraph here.",
            {
                "outcome": "stable_document_ready",
                "blocks": [
                    (
                        "paragraph",
                        "A safe and an unsafe bad mixed into one paragraph here.",
                        {
                            "links": [{"text": "safe", "href": "https://example.com/s"}],
                            "stripped_links": [
                                {
                                    "text": "bad",
                                    "href": "javascript:alert(1)",
                                    "reason": "unsafe_protocol",
                                }
                            ],
                            "inline_marks": [
                                {
                                    "type": "link",
                                    "start": 2,
                                    "end": 6,
                                    "href": "https://example.com/s",
                                }
                            ],
                        },
                    )
                ],
                "warning_codes": ["unsafe_link_protocol"],
            },
        ),
    ]


@pytest.mark.parametrize(
    ("case_name", "source", "expected"),
    _link_baseline_cases(),
    ids=[name for name, _, _ in _link_baseline_cases()],
)
def test_ordinary_link_baseline_byte_identical(
    case_name: str, source: str, expected: dict[str, Any]
) -> None:
    result = _parse(source)

    assert result.outcome == expected["outcome"]
    assert _warning_codes(result) == expected["warning_codes"]
    actual_blocks = [(b.block_type, b.text_content, b.payload_json) for b in result.blocks]
    expected_blocks = [(bt, text, payload) for bt, text, payload in expected["blocks"]]
    assert actual_blocks == expected_blocks, case_name


def test_footnote_and_plain_text_baseline_unchanged() -> None:
    result = _parse(
        "Footnote usage case [^1] with text for the ratio.\n\n[^1]: Footnote body text."
    )

    assert result.outcome == "candidate_document_required"
    assert _warning_codes(result) == ["footnote_reference"]
    assert [b.block_type for b in result.blocks] == ["paragraph", "footnote"]


# ---------------------------------------------------------------------------
# F. 版本身份（§13 G2a-A F 项）
# ---------------------------------------------------------------------------


def test_parser_and_normalizer_identity_bumped() -> None:
    assert PARSER_VERSION == "v2"
    assert NORMALIZER_VERSION == "d6_i3b_structured_source_v2"


def test_parse_result_carries_v2_identity() -> None:
    result = _parse("plain text paragraph.")
    assert result.parser_version == "v2"


# ---------------------------------------------------------------------------
# G. Candidate 持久化贯通（隔离 PostgreSQL）—— blocks_json → confirm →
# freeze/readback。fixture / helper 复用
# tests/test_confirmed_source_lifecycle_db.py（per-test 隔离 schema）。
# ---------------------------------------------------------------------------

_CANDIDATE_IMAGE_CELL_MD = (
    _ENGLISH_PARAGRAPH
    + "[^1]\n\n"
    + "| figure | note |\n"
    + "| --- | --- |\n"
    + '| ![diagram alt](https://example.com/cell.png "Cell Title") '
    + "| supporting note |\n\n"
    + "[^1]: The archival note keeps the additional context attached.\n"
)


async def test_candidate_image_cell_blocks_json_confirm_freeze(
    db_env: asyncpg.Pool,
) -> None:
    """贯通：parse → candidate blocks_json → confirm → freeze/readback。

    footnote（既有非图片 suitability reason）触发 candidate；image-only
    table_cell 以 metadata_only policy 持久化，图片信息不进入 canonical
    text；confirm 不重跑 gate（stable blocks 与 candidate blocks_json
    结构逐块一致）。
    """
    user_id = await _insert_user(db_env)
    created = await _create_candidate(db_env, user_id, _CANDIDATE_IMAGE_CELL_MD)

    # footnote 触发 candidate；图片存在本身不产生任何 reason。
    assert created.suitability.outcome == "candidate_document_required"
    reasons = [str(r) for r in created.suitability.reasons]
    assert any("footnote" in r.lower() for r in reasons), reasons
    assert not any("image" in r.lower() for r in reasons), reasons

    async with db_env.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT blocks_json FROM candidate_reading_documents WHERE id = $1",
            created.candidate_document_id,
        )
    assert row is not None
    blocks = _json(row["blocks_json"])

    cells = [b for b in blocks if b["block_type"] == "table_cell"]
    image_cells = [c for c in cells if c["payload_json"].get("inline_images")]
    assert len(image_cells) == 1
    cell = image_cells[0]
    assert cell["text_content"] is None
    # §7.2 冻结字段集：title 键无条件存在。
    assert cell["payload_json"]["inline_images"] == [
        {
            "source_url": "https://example.com/cell.png",
            "alt_text": "diagram alt",
            "title": "Cell Title",
            "before_utf16": 0,
        }
    ]
    policy = cell["interpretation_policy"]
    for key, value in _TABLE_CELL_METADATA_ONLY_POLICY.items():
        assert policy[key] == value, policy
    assert policy["rag_eligible"] is False

    confirmed = await _confirm(
        db_env,
        record_id=created.reading_record_id,
        candidate_id=created.candidate_document_id,
        user_id=user_id,
    )
    assert confirmed.candidate_confirmed is True

    async with db_env.acquire() as conn:
        stable_rows = await conn.fetch(
            """
            SELECT b.block_id, b.parent_block_id, b.order_index,
                   b.block_type, b.text_content, b.payload_json,
                   b.interpretation_policy_json
            FROM stable_reading_documents d
            JOIN stable_document_blocks b
              ON b.stable_document_id = d.id
            WHERE d.reading_record_id = $1
            ORDER BY b.order_index ASC
            """,
            created.reading_record_id,
        )
        base_text = await conn.fetchval(
            "SELECT text FROM reading_bases WHERE reading_record_id = $1 AND status = 'active'",
            created.reading_record_id,
        )
        frozen_at = await conn.fetchval(
            "SELECT frozen_at FROM confirmed_source_documents "
            "WHERE reading_record_id = $1 AND record_generation = 1",
            created.reading_record_id,
        )

    # source 已 freeze。
    assert frozen_at is not None

    # confirm 不重跑 gate/parse：stable blocks 与 candidate blocks_json
    # 逐块结构一致（id / type / order / parent / text）。
    assert len(stable_rows) == len(blocks)
    for srow, candidate_block in zip(stable_rows, blocks, strict=True):
        assert str(srow["block_id"]) == candidate_block["block_id"]
        assert str(srow["block_type"]) == candidate_block["block_type"]
        assert int(srow["order_index"]) == candidate_block["order_index"]
        assert srow["parent_block_id"] == candidate_block["parent_block_id"]
        assert srow["text_content"] == candidate_block["text_content"]

    stable_cells = [
        r for r in stable_rows if r["block_type"] == "table_cell" and r["text_content"] is None
    ]
    assert len(stable_cells) == 1
    stable_payload = _json(stable_cells[0]["payload_json"])
    assert stable_payload["inline_images"] == [
        {
            "source_url": "https://example.com/cell.png",
            "alt_text": "diagram alt",
            "title": "Cell Title",
            "before_utf16": 0,
        }
    ]
    stable_policy = _json(stable_cells[0]["interpretation_policy_json"])
    for key, value in _TABLE_CELL_METADATA_ONLY_POLICY.items():
        assert stable_policy[key] == value, stable_policy

    # 图片 alt / url / title 不进入 canonical text。
    canonical = str(base_text)
    assert "diagram alt" not in canonical
    assert "cell.png" not in canonical
    assert "Cell Title" not in canonical
