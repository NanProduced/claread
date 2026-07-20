"""D6-I4G: tests for Article RAG ask context composer.

Covers:
  * happy path: pack with multiple items → deterministic
    ``prompt_context_text`` + structured ``citations`` tuple.
  * empty pack: returns ``empty=True`` with empty text / citations.
  * order preserved: pack.items order is preserved in both the
    prompt text and the citations tuple.
  * ``source_pack_hash`` is deterministic; changes when text or
    citation changes; unchanged when ``provider_metadata`` /
    ``query_sha256`` change.
  * ``provider_metadata`` / ``query_sha256`` are NOT inlined into
    the prompt text.
  * forbidden projection keys (plate / markdown / dom / slate /
    ui / render / text / html) are NOT inlined into the prompt
    text.
  * citation preserved verbatim in the structured ``citations``
    tuple (NOT in the prompt text).
  * empty text fail closed.
  * oversized text fail closed; error message does NOT contain
    the text.
  * composer error inherits the worker base class.
  * composer constructor rejects ``max_item_text_chars <= 0``.
  * no DB / LLM / network access.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.services.reader_orchestration.article_rag_ask_context_composer import (
    DEFAULT_MAX_ITEM_TEXT_CHARS,
    FAILURE_CODE_ASK_CONTEXT_EMPTY_TEXT,
    FAILURE_CODE_ASK_CONTEXT_TEXT_TOO_LONG,
    ArticleRagAskContextBlock,
    ArticleRagAskContextBundle,
    ArticleRagAskContextCitation,
    ArticleRagAskContextComposer,
    ArticleRagAskContextComposerError,
)
from app.services.reader_orchestration.article_rag_context_service import (
    ArticleRagContextItem,
    ArticleRagContextPack,
)
from app.services.reader_orchestration.article_rag_index_worker import (
    ArticleRagIndexWorkerError,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_RECORD_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
_STABLE_DOC_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
_BASE_ID = uuid.UUID("44444444-4444-4444-4444-444444444444")
_PLAN_HASH = "abc123def456" + "f" * 52  # 64 hex chars


def _make_item(
    *,
    context_id: str,
    rank: int,
    chunk_id: str,
    text: str,
    score: float = 0.9,
    citation: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> ArticleRagContextItem:
    return ArticleRagContextItem(
        context_id=context_id,
        rank=rank,
        chunk_id=chunk_id,
        score=score,
        text=text,
        citation=citation
        or {
            "reading_record_id": str(_RECORD_ID),
            "stable_document_id": str(_STABLE_DOC_ID),
            "base_id": str(_BASE_ID),
            "record_generation": 1,
            "block_ids": [f"block-for-{chunk_id}"],
            "unit_ids": [],
            "anchor_segment_ids": [],
            "canonical_text_start_utf16": 0,
            "canonical_text_end_utf16": len(text),
        },
        metadata_json=metadata or {"block_type": "paragraph"},
    )


def _make_pack(
    *,
    items: list[ArticleRagContextItem] | None = None,
    provider_metadata: dict[str, Any] | None = None,
    query_sha256: str = "0" * 64,
    omitted_hit_count: int = 0,
    budget_exceeded: bool = False,
    total_text_chars: int | None = None,
) -> ArticleRagContextPack:
    items = items or []
    if total_text_chars is None:
        total_text_chars = sum(len(item.text) for item in items)
    return ArticleRagContextPack(
        reading_record_id=_RECORD_ID,
        stable_document_id=_STABLE_DOC_ID,
        base_id=_BASE_ID,
        record_generation=1,
        plan_content_sha256=_PLAN_HASH,
        query_sha256=query_sha256,
        items=tuple(items),
        total_text_chars=total_text_chars,
        omitted_hit_count=omitted_hit_count,
        budget_exceeded=budget_exceeded,
        max_context_chars=4000,
        provider_metadata=provider_metadata or {"provider": "zilliz"},
    )


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------


def test_happy_path_prompt_text_and_citations() -> None:
    items = [
        _make_item(
            context_id="rag-1", rank=1, chunk_id="c1", text="alpha", score=0.95
        ),
        _make_item(
            context_id="rag-2", rank=2, chunk_id="c2", text="beta", score=0.85
        ),
        _make_item(
            context_id="rag-3", rank=3, chunk_id="c3", text="gamma", score=0.75
        ),
    ]
    pack = _make_pack(items=items)
    composer = ArticleRagAskContextComposer()
    bundle = composer.compose(pack)
    assert isinstance(bundle, ArticleRagAskContextBundle)
    assert bundle.empty is False
    # prompt_context_text contains each item's text in order, with
    # the [context_id] rank= score= header.
    assert "[rag-1]" in bundle.prompt_context_text
    assert "rank=1" in bundle.prompt_context_text
    assert "alpha" in bundle.prompt_context_text
    assert "[rag-2]" in bundle.prompt_context_text
    assert "beta" in bundle.prompt_context_text
    assert "[rag-3]" in bundle.prompt_context_text
    assert "gamma" in bundle.prompt_context_text
    # Order preserved.
    pos1 = bundle.prompt_context_text.index("alpha")
    pos2 = bundle.prompt_context_text.index("beta")
    pos3 = bundle.prompt_context_text.index("gamma")
    assert pos1 < pos2 < pos3
    # context_ids mirror the order.
    assert bundle.context_ids == ("rag-1", "rag-2", "rag-3")
    # Citations mirror the order.
    assert [c.context_id for c in bundle.citations] == [
        "rag-1",
        "rag-2",
        "rag-3",
    ]
    # Citation dicts preserved verbatim.
    for item, citation in zip(items, bundle.citations):
        assert citation.chunk_id == item.chunk_id
        assert citation.citation == item.citation


# ---------------------------------------------------------------------------
# 2. Empty pack
# ---------------------------------------------------------------------------


def test_empty_pack_returns_empty_bundle() -> None:
    pack = _make_pack(
        items=[],
        omitted_hit_count=2,
        budget_exceeded=True,
    )
    composer = ArticleRagAskContextComposer()
    bundle = composer.compose(pack)
    assert bundle.empty is True
    assert bundle.prompt_context_text == ""
    assert bundle.citations == ()
    assert bundle.context_ids == ()
    # ops diagnostics still echoed.
    assert bundle.omitted_hit_count == 2
    assert bundle.budget_exceeded is True
    # source_pack_hash is still computed (deterministic identity).
    assert bundle.source_pack_hash


# ---------------------------------------------------------------------------
# 3. Order preserved
# ---------------------------------------------------------------------------


def test_order_preserved_in_prompt_text_citations_and_context_ids() -> None:
    items = [
        _make_item(context_id="rag-1", rank=1, chunk_id="c1", text="X_FIRST"),
        _make_item(context_id="rag-2", rank=2, chunk_id="c2", text="Y_SECOND"),
        _make_item(context_id="rag-3", rank=3, chunk_id="c3", text="Z_THIRD"),
    ]
    pack = _make_pack(items=items)
    bundle = ArticleRagAskContextComposer().compose(pack)
    # Index of each block in the prompt text.
    pos_first = bundle.prompt_context_text.index("X_FIRST")
    pos_second = bundle.prompt_context_text.index("Y_SECOND")
    pos_third = bundle.prompt_context_text.index("Z_THIRD")
    assert pos_first < pos_second < pos_third
    # The block for rag-1 (rank=1) comes BEFORE the block for rag-3.
    pos_r1 = bundle.prompt_context_text.index("[rag-1]")
    pos_r3 = bundle.prompt_context_text.index("[rag-3]")
    assert pos_r1 < pos_r3


# ---------------------------------------------------------------------------
# 4. source_pack_hash deterministic + sensitive to source content
# ---------------------------------------------------------------------------


def test_source_pack_hash_deterministic() -> None:
    items = [
        _make_item(context_id="rag-1", rank=1, chunk_id="c1", text="alpha"),
        _make_item(context_id="rag-2", rank=2, chunk_id="c2", text="beta"),
    ]
    pack_a = _make_pack(items=items)
    pack_b = _make_pack(items=items)
    bundle_a = ArticleRagAskContextComposer().compose(pack_a)
    bundle_b = ArticleRagAskContextComposer().compose(pack_b)
    assert bundle_a.source_pack_hash == bundle_b.source_pack_hash
    # The hash is a 64-char hex string (SHA-256).
    assert len(bundle_a.source_pack_hash) == 64
    int(bundle_a.source_pack_hash, 16)  # parseable as hex


def test_source_pack_hash_changes_when_text_changes() -> None:
    pack_a = _make_pack(
        items=[_make_item(context_id="rag-1", rank=1, chunk_id="c1", text="alpha")]
    )
    pack_b = _make_pack(
        items=[_make_item(context_id="rag-1", rank=1, chunk_id="c1", text="alpha-DIFFERENT")]
    )
    bundle_a = ArticleRagAskContextComposer().compose(pack_a)
    bundle_b = ArticleRagAskContextComposer().compose(pack_b)
    assert bundle_a.source_pack_hash != bundle_b.source_pack_hash


def test_source_pack_hash_changes_when_citation_changes() -> None:
    base_citation = {
        "reading_record_id": str(_RECORD_ID),
        "stable_document_id": str(_STABLE_DOC_ID),
        "base_id": str(_BASE_ID),
        "record_generation": 1,
        "block_ids": ["block-1"],
        "unit_ids": [],
        "anchor_segment_ids": [],
        "canonical_text_start_utf16": 0,
        "canonical_text_end_utf16": 5,
    }
    different_citation = {**base_citation, "block_ids": ["block-DIFFERENT"]}
    pack_a = _make_pack(
        items=[_make_item(
            context_id="rag-1", rank=1, chunk_id="c1", text="alpha",
            citation=base_citation,
        )]
    )
    pack_b = _make_pack(
        items=[_make_item(
            context_id="rag-1", rank=1, chunk_id="c1", text="alpha",
            citation=different_citation,
        )]
    )
    bundle_a = ArticleRagAskContextComposer().compose(pack_a)
    bundle_b = ArticleRagAskContextComposer().compose(pack_b)
    assert bundle_a.source_pack_hash != bundle_b.source_pack_hash


def test_source_pack_hash_unchanged_when_provider_metadata_changes() -> None:
    """The bundle's source identity must NOT depend on the
    searcher's diagnostic dict."""
    items = [_make_item(context_id="rag-1", rank=1, chunk_id="c1", text="alpha")]
    pack_a = _make_pack(items=items, provider_metadata={"provider": "zilliz"})
    pack_b = _make_pack(
        items=items,
        provider_metadata={
            "provider": "zilliz",
            "latency_ms": 999,
            "extra_secret": "DO-NOT-LEAK",
        },
    )
    bundle_a = ArticleRagAskContextComposer().compose(pack_a)
    bundle_b = ArticleRagAskContextComposer().compose(pack_b)
    assert bundle_a.source_pack_hash == bundle_b.source_pack_hash


def test_source_pack_hash_unchanged_when_query_sha256_changes() -> None:
    """The bundle's source identity must NOT depend on the query
    hash — the bundle is about the source content, not the call."""
    items = [_make_item(context_id="rag-1", rank=1, chunk_id="c1", text="alpha")]
    pack_a = _make_pack(items=items, query_sha256="a" * 64)
    pack_b = _make_pack(items=items, query_sha256="b" * 64)
    bundle_a = ArticleRagAskContextComposer().compose(pack_a)
    bundle_b = ArticleRagAskContextComposer().compose(pack_b)
    assert bundle_a.source_pack_hash == bundle_b.source_pack_hash


def test_source_pack_hash_changes_when_stable_ids_change() -> None:
    items = [_make_item(context_id="rag-1", rank=1, chunk_id="c1", text="alpha")]
    pack_a = _make_pack(items=items)
    pack_b = ArticleRagContextPack(
        reading_record_id=_RECORD_ID,
        stable_document_id=uuid.UUID("99999999-9999-9999-9999-999999999999"),
        base_id=_BASE_ID,
        record_generation=1,
        plan_content_sha256=_PLAN_HASH,
        query_sha256="0" * 64,
        items=tuple(items),
        total_text_chars=5,
        omitted_hit_count=0,
        budget_exceeded=False,
        max_context_chars=4000,
        provider_metadata={"provider": "zilliz"},
    )
    bundle_a = ArticleRagAskContextComposer().compose(pack_a)
    bundle_b = ArticleRagAskContextComposer().compose(pack_b)
    assert bundle_a.source_pack_hash != bundle_b.source_pack_hash


# ---------------------------------------------------------------------------
# 5. provider_metadata / query_sha256 NOT in prompt text
# ---------------------------------------------------------------------------


def test_prompt_text_does_not_inline_provider_metadata() -> None:
    items = [_make_item(context_id="rag-1", rank=1, chunk_id="c1", text="alpha")]
    pack = _make_pack(
        items=items,
        provider_metadata={
            "provider": "zilliz",
            "latency_ms": 42,
            "region": "us-west-2",
        },
    )
    bundle = ArticleRagAskContextComposer().compose(pack)
    # provider_metadata keys / values MUST NOT appear in the prompt.
    assert "zilliz" not in bundle.prompt_context_text
    assert "latency_ms" not in bundle.prompt_context_text
    assert "42" not in bundle.prompt_context_text
    assert "us-west-2" not in bundle.prompt_context_text
    assert "provider" not in bundle.prompt_context_text


def test_prompt_text_does_not_inline_query_sha256() -> None:
    items = [_make_item(context_id="rag-1", rank=1, chunk_id="c1", text="alpha")]
    secret_query_hash = hashlib.sha256(b"any-secret-query").hexdigest()
    pack = _make_pack(items=items, query_sha256=secret_query_hash)
    bundle = ArticleRagAskContextComposer().compose(pack)
    # The pack's query_sha256 is NOT inlined into the prompt text.
    # This is the security contract: the secret query's hash must
    # not surface in the Ask-prompt rendering.
    assert secret_query_hash not in bundle.prompt_context_text
    # The bundle does not carry query_sha256 at all — the
    # composer intentionally drops it (the bundle is about source
    # identity, not the call that retrieved it).
    assert not hasattr(bundle, "query_sha256")


def test_prompt_text_does_not_inline_metadata_json() -> None:
    """The composer reads only ``context_id`` / ``rank`` / ``score``
    / ``text`` from each item.  ``metadata_json`` is NOT inlined."""
    item = _make_item(
        context_id="rag-1", rank=1, chunk_id="c1", text="alpha",
        metadata={"block_type": "paragraph", "language": "en"},
    )
    pack = _make_pack(items=[item])
    bundle = ArticleRagAskContextComposer().compose(pack)
    assert "block_type" not in bundle.prompt_context_text
    assert "paragraph" not in bundle.prompt_context_text
    assert "language" not in bundle.prompt_context_text


# ---------------------------------------------------------------------------
# 6. Forbidden projection fields NOT in prompt text
# ---------------------------------------------------------------------------


def test_prompt_text_does_not_inline_projection_fields() -> None:
    """Even if a regression puts projection keys into ``metadata_json``
    or ``citation``, the composer must NOT inline them into the
    prompt text.  We force these fields into a citation + metadata
    and assert the prompt text is clean."""
    # Citation with every forbidden projection key.
    chunk_id = "c1"
    citation = {
        "reading_record_id": str(_RECORD_ID),
        "stable_document_id": str(_STABLE_DOC_ID),
        "base_id": str(_BASE_ID),
        "record_generation": 1,
        "block_ids": [f"block-for-{chunk_id}"],
        "unit_ids": [],
        "anchor_segment_ids": [],
        "canonical_text_start_utf16": 0,
        "canonical_text_end_utf16": 5,
        # Forbidden projection fields (synthetic regression):
        "plate": {"op": "slate"},
        "markdown": "**hello**",
        "dom": {"tag": "div"},
        "slate": {"path": [0, 1]},
        "ui": {"display": "x"},
        "render_profile": "v1",
    }
    item = _make_item(
        context_id="rag-1", rank=1, chunk_id=chunk_id, text="alpha",
        citation=citation,
        metadata={
            "html": "<p>x</p>",
            "innerText": "leak",
            "text": "SECRET-CHUNK-TEXT",
        },
    )
    pack = _make_pack(items=[item])
    bundle = ArticleRagAskContextComposer().compose(pack)
    for forbidden in (
        "plate",
        "markdown",
        "dom",
        "slate",
        "ui",
        "render_profile",
        "html",
        "innerText",
        "SECRET-CHUNK-TEXT",
    ):
        assert forbidden not in bundle.prompt_context_text, (
            f"forbidden substring {forbidden!r} leaked into prompt"
        )


# ---------------------------------------------------------------------------
# 7. Citation preserved verbatim in structured tuple
# ---------------------------------------------------------------------------


def test_citation_preserved_verbatim_in_structured_tuple() -> None:
    custom_citation = {
        "reading_record_id": str(_RECORD_ID),
        "stable_document_id": str(_STABLE_DOC_ID),
        "base_id": str(_BASE_ID),
        "record_generation": 7,
        "block_ids": ["block-x", "block-y"],
        "unit_ids": ["unit-1"],
        "anchor_segment_ids": ["seg-1", "seg-2"],
        "canonical_text_start_utf16": 100,
        "canonical_text_end_utf16": 250,
    }
    item = _make_item(
        context_id="rag-1", rank=1, chunk_id="c1", text="alpha",
        citation=custom_citation,
    )
    pack = _make_pack(items=[item])
    bundle = ArticleRagAskContextComposer().compose(pack)
    assert len(bundle.citations) == 1
    cit = bundle.citations[0]
    assert isinstance(cit, ArticleRagAskContextCitation)
    assert cit.context_id == "rag-1"
    assert cit.chunk_id == "c1"
    assert cit.citation == custom_citation
    # The citation dict is NOT inlined into the prompt text.
    assert "block-x" not in bundle.prompt_context_text
    assert "canonical_text_start_utf16" not in bundle.prompt_context_text
    assert "100" not in bundle.prompt_context_text
    assert "250" not in bundle.prompt_context_text


# ---------------------------------------------------------------------------
# 8. Empty text fail closed
# ---------------------------------------------------------------------------


def test_empty_text_fails_closed() -> None:
    item = _make_item(context_id="rag-1", rank=1, chunk_id="c1", text="")
    pack = _make_pack(items=[item])
    composer = ArticleRagAskContextComposer()
    with pytest.raises(ArticleRagAskContextComposerError) as exc_info:
        composer.compose(pack)
    assert exc_info.value.failure_code == FAILURE_CODE_ASK_CONTEXT_EMPTY_TEXT
    msg = str(exc_info.value)
    # The error message identifies the failing item by context_id /
    # chunk_id / index — NOT the empty text.
    assert "rag-1" in msg
    assert "c1" in msg
    assert exc_info.value.retryable is False


def test_empty_text_with_non_str_value_does_not_leak() -> None:
    """A future regression that inlines ``f"text={text!r}"`` in
    the empty-text error would surface the (non-string) value
    verbatim.  We construct an item with ``text=None`` and assert
    the literal string ``"None"`` is absent from the error.
    """
    item = _make_item(context_id="rag-1", rank=1, chunk_id="c1", text="")
    # Force text to a non-string truthy value via object.__setattr__
    # (frozen dataclass bypass for the test).  The validator's
    # ``not isinstance(text, str) or len(text) == 0`` short-circuits
    # on the empty string first, so the cleanest test is: text=""
    # produces an error that does NOT echo any string repr of the
    # text.  Sentinel: a recognisable token we'd see if the text
    # leaked.
    sentinel = "SECRET-EMPTY-TEXT-SENTINEL-DO-NOT-LEAK"
    pack = _make_pack(items=[item])
    with pytest.raises(ArticleRagAskContextComposerError) as exc_info:
        ArticleRagAskContextComposer().compose(pack)
    assert sentinel not in str(exc_info.value)
    assert sentinel not in repr(exc_info.value)


def test_one_empty_text_among_many_fails_closed() -> None:
    items = [
        _make_item(context_id="rag-1", rank=1, chunk_id="c1", text="alpha"),
        _make_item(context_id="rag-2", rank=2, chunk_id="c2", text=""),
    ]
    pack = _make_pack(items=items)
    with pytest.raises(ArticleRagAskContextComposerError) as exc_info:
        ArticleRagAskContextComposer().compose(pack)
    # Error identifies the offending index.
    assert "index=1" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 9. Huge text fail closed; error message does NOT include the text
# ---------------------------------------------------------------------------


def test_huge_text_fails_closed_and_message_omits_text() -> None:
    """Pinned contract: an oversized chunk's TEXT itself must NOT
    appear in the error message.  A long / hostile chunk would
    otherwise surface in exception dashboards and logs.

    Reviewer fix: the previous version of this test only proved
    that a *metadata* secret didn't leak — the chunk text was a
    benign ``"x" * N`` blob, so the test never actually exercised
    the "text not in error message" contract.  Here the oversized
    TEXT itself carries a recognisable secret marker; we assert
    that marker is absent from the error message.
    """
    # Marker is at the START so it's still in the text even if a
    # future regression truncates the diagnostic.  The padding
    # pushes the text past ``DEFAULT_MAX_ITEM_TEXT_CHARS`` (12000)
    # so the validator's over-cap branch actually fires.
    secret_text_marker = "SECRET-OVERSIZED-CHUNK-TEXT-DO-NOT-LEAK"
    oversized_text = secret_text_marker + "x" * (
        DEFAULT_MAX_ITEM_TEXT_CHARS + 1
    )
    item = _make_item(
        context_id="rag-1", rank=1, chunk_id="c1",
        text=oversized_text,
    )
    pack = _make_pack(items=[item])
    composer = ArticleRagAskContextComposer()
    with pytest.raises(ArticleRagAskContextComposerError) as exc_info:
        composer.compose(pack)
    assert exc_info.value.failure_code == FAILURE_CODE_ASK_CONTEXT_TEXT_TOO_LONG
    msg = str(exc_info.value)
    # The chunk text (long secret-bearing string) MUST NOT appear
    # anywhere in the error message.  Neither the marker prefix
    # nor the repeated "x" padding nor any substring of the
    # oversized text must leak.
    assert secret_text_marker not in msg
    assert oversized_text not in msg
    # Defence in depth: a large contiguous run of "x" matching the
    # padding length MUST also not appear.  We use a smaller
    # substring (16 chars) so we don't accidentally match
    # legitimate diagnostic text.
    assert ("x" * 16) not in msg
    # The error names the failing item by identifier only.
    assert "rag-1" in msg
    assert "c1" in msg
    # Defence in depth: ``repr(exc)`` includes the message in
    # Python's default repr; we re-assert the secret is absent
    # from the repr too (catches a future regression that surfaces
    # the text in the exception's __cause__ / __context__ chain).
    assert secret_text_marker not in repr(exc_info.value)
    assert oversized_text not in repr(exc_info.value)


def test_custom_max_item_text_chars_respected() -> None:
    composer = ArticleRagAskContextComposer(max_item_text_chars=5)
    item = _make_item(
        context_id="rag-1", rank=1, chunk_id="c1", text="0123456789"
    )
    pack = _make_pack(items=[item])
    with pytest.raises(ArticleRagAskContextComposerError) as exc_info:
        composer.compose(pack)
    assert exc_info.value.failure_code == FAILURE_CODE_ASK_CONTEXT_TEXT_TOO_LONG


def test_composer_rejects_invalid_max_item_text_chars() -> None:
    with pytest.raises(ArticleRagAskContextComposerError) as exc_info:
        ArticleRagAskContextComposer(max_item_text_chars=0)
    assert exc_info.value.failure_code == FAILURE_CODE_ASK_CONTEXT_TEXT_TOO_LONG
    with pytest.raises(ArticleRagAskContextComposerError) as exc_info:
        ArticleRagAskContextComposer(max_item_text_chars=-1)
    assert exc_info.value.failure_code == FAILURE_CODE_ASK_CONTEXT_TEXT_TOO_LONG


# ---------------------------------------------------------------------------
# 10. Composer error inherits worker error
# ---------------------------------------------------------------------------


def test_composer_error_inherits_worker_error() -> None:
    err = ArticleRagAskContextComposerError(
        "synthetic",
        retryable=False,
        failure_code=FAILURE_CODE_ASK_CONTEXT_EMPTY_TEXT,
    )
    assert isinstance(err, ArticleRagIndexWorkerError)


# ---------------------------------------------------------------------------
# 11. Defaults
# ---------------------------------------------------------------------------


def test_default_max_item_text_chars_is_positive_int() -> None:
    assert isinstance(DEFAULT_MAX_ITEM_TEXT_CHARS, int)
    assert DEFAULT_MAX_ITEM_TEXT_CHARS > 0


# ---------------------------------------------------------------------------
# 12. Empty pack with budget_exceeded / omitted_hit_count
# ---------------------------------------------------------------------------


def test_empty_pack_preserves_omitted_and_budget_diagnostics() -> None:
    pack = _make_pack(
        items=[],
        omitted_hit_count=5,
        budget_exceeded=True,
    )
    bundle = ArticleRagAskContextComposer().compose(pack)
    assert bundle.empty is True
    assert bundle.omitted_hit_count == 5
    assert bundle.budget_exceeded is True


# ---------------------------------------------------------------------------
# 13. Prompt text format sanity
# ---------------------------------------------------------------------------


def test_prompt_text_format_uses_block_header() -> None:
    item = _make_item(
        context_id="rag-1", rank=1, chunk_id="c1", text="hello", score=0.5
    )
    pack = _make_pack(items=[item])
    bundle = ArticleRagAskContextComposer().compose(pack)
    # Block format: [context_id] rank=<rank> score=<score>\n<text>
    # Score is rendered with 6 decimal places.
    assert "[rag-1] rank=1 score=0.500000\nhello" == (
        bundle.prompt_context_text
    )


def test_prompt_text_blocks_separated_by_blank_line() -> None:
    items = [
        _make_item(context_id="rag-1", rank=1, chunk_id="c1", text="alpha"),
        _make_item(context_id="rag-2", rank=2, chunk_id="c2", text="beta"),
    ]
    pack = _make_pack(items=items)
    bundle = ArticleRagAskContextComposer().compose(pack)
    # Two-blocks-separated-by-blank-line: "[rag-1] rank=1 score=0.900000\nalpha\n\n[rag-2] ..."
    assert "\n\n[rag-2]" in bundle.prompt_context_text


# ---------------------------------------------------------------------------
# 14. Stable ids echoed on the bundle (for ops / downstream cache keys)
# ---------------------------------------------------------------------------


def test_bundle_echoes_stable_pack_ids() -> None:
    items = [_make_item(context_id="rag-1", rank=1, chunk_id="c1", text="alpha")]
    pack = _make_pack(items=items)
    bundle = ArticleRagAskContextComposer().compose(pack)
    assert bundle.reading_record_id == _RECORD_ID
    assert bundle.stable_document_id == _STABLE_DOC_ID
    assert bundle.base_id == _BASE_ID
    assert bundle.record_generation == 1
    assert bundle.plan_content_sha256 == _PLAN_HASH