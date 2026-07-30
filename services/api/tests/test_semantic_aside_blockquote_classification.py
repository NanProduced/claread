"""Aside / GFM alert / blockquote end-to-end classification fixtures."""

from __future__ import annotations

from app.services.reader_orchestration.automatic_layer_policy import (
    resolve_policy_for_stable_block,
)
from app.services.reader_orchestration.input_document_normalizer import (
    normalize_input_document,
)
from app.services.reader_orchestration.input_suitability_gate import (
    InputSuitabilityRequest,
)
from app.services.reader_orchestration.markdown_source_parser import MarkdownSourceParser
from app.services.reader_orchestration.semantic_classifier import SEMANTIC_CONTRACT_V1

_PAD = (
    " The surrounding paragraphs add enough English words for the suitability "
    "gate so these short structural samples still freeze as stable documents "
    "during continuous integration fixture runs without triggering short-content rejection."
)


def _english_pad(body: str) -> str:
    return body + "\n\n" + _PAD * 3


def test_parser_notion_aside_emits_hint_and_blockquote() -> None:
    md = _english_pad(
        '<aside class="note">This aside carries a genuine reading note about the chapter.</aside>'
    )
    result = MarkdownSourceParser().parse(md)
    aside_blocks = [
        b
        for b in result.blocks
        if (b.payload_json or {}).get("source_semantic_hint") == "html_aside"
    ]
    assert aside_blocks, "parser must set source_semantic_hint=html_aside for <aside>"
    assert aside_blocks[0].block_type == "blockquote"


def test_parser_div_not_aside() -> None:
    md = _english_pad(
        '<div class="note">This is an ordinary div, not an aside container.</div>'
    )
    result = MarkdownSourceParser().parse(md)
    for b in result.blocks:
        assert (b.payload_json or {}).get("source_semantic_hint") != "html_aside"


def test_normalizer_aside_source_callout_t_only() -> None:
    md = _english_pad(
        '<aside class="note">This aside carries a genuine reading note about the chapter.</aside>'
    )
    doc = normalize_input_document(
        InputSuitabilityRequest(text=md, source_type="pasted_text")
    )
    callouts = [
        b
        for b in doc.blocks
        if (b.payload_json or {}).get("semantic", {}).get("content_role")
        == "source_callout"
    ]
    assert callouts
    for b in callouts:
        resolved = resolve_policy_for_stable_block(
            block_type=b.block_type,
            payload_json=b.payload_json,
        )
        assert resolved.policy.translation is True
        assert resolved.policy.vocabulary is False
        assert resolved.policy.grammar_note is False
        assert resolved.policy.sentence_analysis is False
        assert resolved.contract_version == SEMANTIC_CONTRACT_V1


def test_normalizer_gfm_alert_source_callout_t_only() -> None:
    md = _english_pad("> [!NOTE]\n> Remember this tip for readers carefully.")
    doc = normalize_input_document(
        InputSuitabilityRequest(text=md, source_type="pasted_text")
    )
    callouts = [
        b
        for b in doc.blocks
        if (b.payload_json or {}).get("semantic", {}).get("content_role")
        == "source_callout"
    ]
    assert callouts
    for b in callouts:
        pol = resolve_policy_for_stable_block(
            block_type=b.block_type, payload_json=b.payload_json
        ).policy
        assert pol.as_dict() == {
            "translation": True,
            "vocabulary": False,
            "grammar_note": False,
            "sentence_analysis": False,
        }


def test_normalizer_plain_blockquote_quotation_t_only() -> None:
    md = _english_pad("> Someone said something memorable about learning English carefully.")
    doc = normalize_input_document(
        InputSuitabilityRequest(text=md, source_type="pasted_text")
    )
    quotes = [
        b
        for b in doc.blocks
        if (b.payload_json or {}).get("semantic", {}).get("content_role") == "quotation"
    ]
    assert quotes
    for b in quotes:
        semantic = b.payload_json["semantic"]
        assert semantic.get("classification", {}).get("shadow_only") is not True
        pol = resolve_policy_for_stable_block(
            block_type=b.block_type, payload_json=b.payload_json
        ).policy
        assert pol.vocabulary is False
        assert pol.grammar_note is False


def test_ordinary_paragraph_still_prose() -> None:
    md = _english_pad(
        "This ordinary paragraph should remain prose for automatic learning layers."
    )
    doc = normalize_input_document(
        InputSuitabilityRequest(text=md, source_type="pasted_text")
    )
    paras = [b for b in doc.blocks if b.block_type == "paragraph"]
    assert paras
    for b in paras:
        role = (b.payload_json or {}).get("semantic", {}).get("content_role")
        assert role == "prose"
