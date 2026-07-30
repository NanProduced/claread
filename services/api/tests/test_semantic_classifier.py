"""Fixture-first tests for semantic_contract_v1 / semrules_v1 classifier."""

from __future__ import annotations

from app.services.reader_orchestration.semantic_classifier import (
    SEMANTIC_CONTRACT_V1,
    SEMRULES_V1,
    annotate_blocks_with_semantic,
    classify_blocks,
    extract_contract_version,
    is_legacy_semantic,
)


def _block(
    block_type: str,
    text: str | None = None,
    *,
    payload: dict | None = None,
    block_id: str = "b1",
) -> dict:
    return {
        "block_id": block_id,
        "block_type": block_type,
        "text_content": text,
        "payload_json": payload or {},
    }


def test_structural_types_get_contract_with_null_role() -> None:
    blocks = [
        _block("heading", "Title", block_id="b1"),
        _block("code_block", "print(1)", payload={"language": "python"}, block_id="b2"),
        _block("table_cell", "42", block_id="b3"),
    ]
    results = classify_blocks(blocks)
    assert all(r.contract_version == SEMANTIC_CONTRACT_V1 for r in results)
    assert all(r.content_role is None for r in results)
    # content_role null is NOT legacy — only missing contract_version is.
    for r in results:
        payload = {"semantic": r.to_payload()}
        assert extract_contract_version(payload) == SEMANTIC_CONTRACT_V1
        assert not is_legacy_semantic(payload)


def test_legacy_is_missing_contract_version_only() -> None:
    assert is_legacy_semantic({})
    assert is_legacy_semantic({"semantic": {}})
    assert is_legacy_semantic(None)
    assert not is_legacy_semantic(
        {"semantic": {"contract_version": SEMANTIC_CONTRACT_V1, "content_role": None}}
    )


def test_references_section_citation_enforce() -> None:
    blocks = [
        _block("heading", "References", block_id="b1"),
        _block(
            "paragraph",
            "[1] Smith, J. Example paper. Journal, 2020.",
            block_id="b2",
        ),
        _block("list_item", "Jones et al. Another work.", block_id="b3"),
    ]
    results = classify_blocks(blocks)
    assert results[0].content_role is None  # heading
    assert results[1].content_role == "citation_reference"
    assert results[1].shadow_only is False
    assert results[1].rules_version == SEMRULES_V1
    assert "section_heading:references" in results[1].signals
    assert results[2].content_role == "citation_reference"


def test_bibliography_heading_chinese() -> None:
    blocks = [
        _block("heading", "参考文献", block_id="b1"),
        _block("paragraph", "张三. 某文. 2021.", block_id="b2"),
    ]
    results = classify_blocks(blocks)
    assert results[1].content_role == "citation_reference"
    assert results[1].shadow_only is False


def test_weak_numbered_citation_is_shadow_only() -> None:
    blocks = [
        _block("paragraph", "Intro prose without a references heading.", block_id="b1"),
        _block("paragraph", "[1] Orphan citation-looking line.", block_id="b2"),
    ]
    results = classify_blocks(blocks)
    assert results[0].content_role == "prose"
    assert results[1].content_role == "citation_reference"
    assert results[1].shadow_only is True


def test_link_only_paragraph_enforce() -> None:
    blocks = [
        _block(
            "paragraph",
            "See the docs",
            payload={
                "links": [
                    {"text": "See the docs", "href": "https://example.com/docs"},
                ]
            },
            block_id="b1",
        )
    ]
    results = classify_blocks(blocks)
    assert results[0].content_role == "link_only"
    assert results[0].shadow_only is False


def test_inline_link_in_prose_stays_prose() -> None:
    blocks = [
        _block(
            "paragraph",
            "Please see the docs for more background on the topic.",
            payload={
                "links": [
                    {"text": "docs", "href": "https://example.com/docs"},
                ]
            },
            block_id="b1",
        )
    ]
    results = classify_blocks(blocks)
    assert results[0].content_role == "prose"


def test_gfm_alert_source_callout_enforce() -> None:
    blocks = [
        _block("blockquote", "[!NOTE]\nRemember this tip.", block_id="b1"),
    ]
    results = classify_blocks(blocks)
    assert results[0].content_role == "source_callout"
    assert results[0].shadow_only is False


def test_ordinary_div_html_block_not_aside() -> None:
    """extracted_from=html_block alone must not become source_callout."""
    blocks = [
        _block(
            "paragraph",
            "A plain HTML fragment that is not an aside.",
            payload={"extracted_from": "html_block"},
            block_id="b1",
        )
    ]
    results = classify_blocks(blocks)
    assert results[0].content_role == "prose"


def test_bare_blockquote_is_enforced_quotation() -> None:
    blocks = [
        _block("blockquote", "Someone said something memorable.", block_id="b1"),
    ]
    results = classify_blocks(blocks)
    assert results[0].content_role == "quotation"
    assert results[0].shadow_only is False


def test_html_aside_hint_is_source_callout() -> None:
    blocks = [
        _block(
            "blockquote",
            "This aside carries a genuine reading note.",
            payload={"source_semantic_hint": "html_aside", "extracted_from": "html_block"},
            block_id="b1",
        )
    ]
    results = classify_blocks(blocks)
    assert results[0].content_role == "source_callout"
    assert results[0].shadow_only is False
    assert "source_semantic_hint:html_aside" in results[0].signals


def test_question_paragraph_is_shadow() -> None:
    blocks = [
        _block("paragraph", "What is the capital of France?", block_id="b1"),
    ]
    results = classify_blocks(blocks)
    assert results[0].content_role == "prompt_question"
    assert results[0].shadow_only is True


def test_ordinary_prose_paragraph() -> None:
    blocks = [
        _block(
            "paragraph",
            "The research group compared three regional pilots carefully.",
            block_id="b1",
        )
    ]
    results = classify_blocks(blocks)
    assert results[0].content_role == "prose"
    assert results[0].shadow_only is False


def test_annotate_stable_document_blocks() -> None:
    from app.schemas.reader_documents import StableDocumentBlock

    blocks = [
        StableDocumentBlock(
            block_id="b1",
            order_index=0,
            block_type="heading",
            text_content="Hello",
            payload_json={"level": 1},
        ),
        StableDocumentBlock(
            block_id="b2",
            order_index=1,
            block_type="paragraph",
            text_content="Body text here.",
            payload_json={},
        ),
    ]
    annotated = annotate_blocks_with_semantic(blocks)
    assert annotated[0].payload_json["semantic"]["contract_version"] == SEMANTIC_CONTRACT_V1
    assert annotated[0].payload_json["semantic"]["content_role"] is None
    assert annotated[1].payload_json["semantic"]["content_role"] == "prose"


def test_references_negative_not_mid_document_heading_alike() -> None:
    """'Our references show that…' body text must not trigger citation."""
    blocks = [
        _block("heading", "Discussion", block_id="b1"),
        _block(
            "paragraph",
            "Our references show that the pilot succeeded overall.",
            block_id="b2",
        ),
    ]
    results = classify_blocks(blocks)
    assert results[1].content_role == "prose"
