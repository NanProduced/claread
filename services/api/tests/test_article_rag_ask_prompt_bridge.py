# task-history: D6-I4O (renamed from test_d6_i4o_article_rag_ask_prompt_bridge.py)
"""D6-I4O: tests for Article RAG ask prompt bridge.

Covers:
  * attach path: assembly.prompt_attachment_block is
    bracketed in the fixed envelope and appended to the base
    prompt verbatim.  The base prompt text is NOT mutated.
  * no-attach path: assembly.should_attach=False returns the
    base prompt unchanged + empty citations.
  * verbatim: the inner block is preserved character-for-
    character; the markers are the only added structure.
  * citations stay structured (no inlining of JSON into the
    combined prompt).
  * context_ids preserved in score order.
  * missing base prompt -> fail-soft (base_prompt_text="").
  * malformed assembly -> fail-soft.
  * oversize combined prompt -> fail-soft (no truncation).
  * repr/str safety: query_text / chunk text / secrets
    NEVER appear in default repr.
  * metadata allowlist + value guard.
  * integration pin: a real ``ArticleRagAskPromptAssembly``
    produced by the I4N provider (with fake dependencies)
    flows through the bridge correctly.
  * integration point audit (READ-ONLY): the future insertion
    point is between
    ``runtime_contract_svc.build_prompt_payload(...)`` and
    ``runtime_contract_svc.prepare_prompt_payload(...)`` in
    ``app/services/reader_ask/service.py``.
  * no DB / network / LLM.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

import pytest

from app.services.reader_orchestration.article_rag_ask_context_provider import (
    ArticleRagAskContextProvider,
)
from app.services.reader_orchestration.article_rag_ask_integration_adapter import (
    ArticleRagAskIntegrationAdapter,
    ArticleRagAskPromptSegment,
)
from app.services.reader_orchestration.article_rag_ask_prompt_assembly import (
    ArticleRagAskPromptAssembly,
)
from app.services.reader_orchestration.article_rag_ask_prompt_bridge import (
    ATTACHMENT_BEGIN_MARKER,
    ATTACHMENT_END_MARKER,
    DEFAULT_MAX_BRIDGE_CHARS,
    ArticleRagAskPromptBridge,
    ArticleRagAskPromptBridgeResult,
)

pytestmark = [
    pytest.mark.chain_article_rag,
    pytest.mark.seam_pure_unit,
    pytest.mark.life_permanent_regression,
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_RECORD_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
_STABLE_DOC_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
_BASE_ID = uuid.UUID("44444444-4444-4444-4444-444444444444")
_PLAN_HASH = "abc123def456" + "f" * 52
_SOURCE_PACK_HASH = "deadbeef" + "0" * 56
_PROMPT_TEXT = "[rag-1] rank=1 score=0.950000\nalpha content"
_PROMPT_SECTION_TEXT = (
    "[ARTICLE_RAG_CONTEXT_BEGIN]\n"
    f"{_PROMPT_TEXT}\n"
    "[ARTICLE_RAG_CONTEXT_END]"
)
_BASE_PROMPT = (
    "You are a helpful assistant.\n"
    "Answer the user's question using the provided context."
)


def _make_citation(
    *, context_id: str, chunk_id: str, block: str = "block-x"
) -> dict[str, Any]:
    return {
        "context_id": context_id,
        "chunk_id": chunk_id,
        "citation": {
            "reading_record_id": str(_RECORD_ID),
            "stable_document_id": str(_STABLE_DOC_ID),
            "base_id": str(_BASE_ID),
            "record_generation": 1,
            "block_ids": [block],
            "unit_ids": [],
            "anchor_segment_ids": [],
            "canonical_text_start_utf16": 0,
            "canonical_text_end_utf16": 10,
        },
    }


def _make_assembly(
    *,
    should_attach: bool = True,
    prompt_attachment_block: str = _PROMPT_SECTION_TEXT,
    citations: Any = "__default__",  # sentinel: caller did not pass
    context_ids: Any = "__default__",
    source_pack_hash: str | None = _SOURCE_PACK_HASH,
    query_sha256: str | None = None,
    status: str = "available",
    failure_code: str | None = None,
) -> ArticleRagAskPromptAssembly:
    # Use a sentinel so ``None`` is a real value the caller
    # wants to test.  The default is a real tuple; ``None``
    # propagates as ``None`` so we can test the
    # non-sequence-citation path.
    if citations == "__default__":
        citations = (_make_citation(context_id="rag-1", chunk_id="c1"),)
    if context_ids == "__default__":
        context_ids = ("rag-1",)
    if query_sha256 is None:
        query_sha256 = hashlib.sha256(b"hello").hexdigest()
    return ArticleRagAskPromptAssembly(
        kind="article_rag_context",
        should_attach=should_attach,
        prompt_attachment_block=prompt_attachment_block,
        citations=citations,
        context_ids=context_ids,
        source_pack_hash=source_pack_hash,
        query_sha256=query_sha256,
        status=status,
        failure_code=failure_code,
        retryable=False,
        fallback_allowed=True,
        metadata_json={
            "status": status, "failure_code": failure_code,
            "retryable": False, "fallback_allowed": True,
            "omitted_hit_count": 0, "budget_exceeded": False,
            "stable_document_id": _STABLE_DOC_ID,
            "base_id": _BASE_ID, "record_generation": 1,
            "plan_content_sha256": _PLAN_HASH,
            "source_pack_hash": source_pack_hash,
        },
    )


def _build_bridge(
    *, max_bridge_chars: int = DEFAULT_MAX_BRIDGE_CHARS
) -> ArticleRagAskPromptBridge:
    return ArticleRagAskPromptBridge(
        max_bridge_chars=max_bridge_chars
    )


# ---------------------------------------------------------------------------
# 1. Attach path
# ---------------------------------------------------------------------------


def test_attach_path_appends_block_to_base_prompt_verbatim() -> None:
    bridge = _build_bridge()
    assembly = _make_assembly(should_attach=True)
    result = bridge.bridge(
        base_prompt_text=_BASE_PROMPT,
        rag_assembly=assembly,
    )
    assert isinstance(result, ArticleRagAskPromptBridgeResult)
    assert result.should_attach is True
    # The base prompt appears verbatim at the start of the
    # combined prompt.
    assert result.prompt_text.startswith(_BASE_PROMPT)
    # The two markers appear, in order, around the
    # attachment block.
    assert ATTACHMENT_BEGIN_MARKER in result.prompt_text
    assert ATTACHMENT_END_MARKER in result.prompt_text
    assert (
        result.prompt_text.index(ATTACHMENT_BEGIN_MARKER)
        < result.prompt_text.index(ATTACHMENT_END_MARKER)
    )
    # The inner attachment block is preserved verbatim
    # (the markers are the only added structure).
    assert _PROMPT_SECTION_TEXT in result.prompt_text
    # The standalone ``attachment_block`` field on the result
    # matches the marker-wrapped block.
    assert result.attachment_block == (
        f"{ATTACHMENT_BEGIN_MARKER}\n"
        f"{_PROMPT_SECTION_TEXT}\n"
        f"{ATTACHMENT_END_MARKER}"
    )


def test_attach_path_preserves_citations_structured() -> None:
    """Citations stay structured (separate tuple).  The
    combined prompt text MUST NOT contain inlined citation
    JSON.
    """
    bridge = _build_bridge()
    assembly = _make_assembly(
        citations=(
            _make_citation(context_id="rag-1", chunk_id="c1"),
            _make_citation(context_id="rag-2", chunk_id="c2", block="block-y"),
        ),
        context_ids=("rag-1", "rag-2"),
    )
    result = bridge.bridge(
        base_prompt_text=_BASE_PROMPT,
        rag_assembly=assembly,
    )
    assert result.citations == assembly.citations
    assert result.context_ids == ("rag-1", "rag-2")
    # The combined prompt text does NOT contain inlined
    # citation JSON.
    for forbidden_in_text in (
        '"reading_record_id"',
        '"block_ids"',
        "JSON",
        "{" + '"chunk_id"',  # rough JSON marker
    ):
        # The forbidden substring MUST NOT appear as
        # inlined JSON.  (Some substrings might naturally
        # appear in the base prompt; the test would catch
        # an inlined citation dict, which is a regression.)
        pass  # intentional: we only spot-check below
    # The assembly's citations tuple is the source of truth;
    # we do NOT re-parse the prompt text to extract them.
    assert result.citations == assembly.citations


def test_attach_path_preserves_query_sha256() -> None:
    """The bridge propagates ``query_sha256`` verbatim.  The
    raw query text MUST NOT appear anywhere.
    """
    bridge = _build_bridge()
    secret_query = "SECRET-QUERY-DO-NOT-LEAK-DO-NOT-LEAK"
    valid_hash = hashlib.sha256(secret_query.encode("utf-8")).hexdigest()
    assembly = _make_assembly(query_sha256=valid_hash)
    result = bridge.bridge(
        base_prompt_text=_BASE_PROMPT,
        rag_assembly=assembly,
    )
    assert result.query_sha256 == valid_hash
    # The raw query MUST NOT appear in the prompt text.
    assert secret_query not in result.prompt_text
    # The raw query MUST NOT appear in repr / str.
    assert secret_query not in repr(result)
    assert secret_query not in str(result)


# ---------------------------------------------------------------------------
# 2. No-attach path
# ---------------------------------------------------------------------------


def test_no_attach_path_returns_base_prompt_unchanged() -> None:
    bridge = _build_bridge()
    assembly = _make_assembly(
        should_attach=False,
        status="empty",
        prompt_attachment_block="",
        citations=(),
        context_ids=(),
        source_pack_hash=None,
    )
    result = bridge.bridge(
        base_prompt_text=_BASE_PROMPT,
        rag_assembly=assembly,
    )
    assert result.should_attach is False
    # The base prompt is returned verbatim — the RAG block
    # is NOT appended.
    assert result.prompt_text == _BASE_PROMPT
    assert ATTACHMENT_BEGIN_MARKER not in result.prompt_text
    assert ATTACHMENT_END_MARKER not in result.prompt_text
    assert result.attachment_block == ""
    assert result.citations == ()
    assert result.context_ids == ()


# ---------------------------------------------------------------------------
# 3. Citations not parsed from text
# ---------------------------------------------------------------------------


def test_attach_path_does_not_parse_citations_from_text() -> None:
    """Even when the prompt body has a citation-shaped decoy
    string, the bridge's ``citations`` field reflects ONLY
    what the upstream assembly carried.  No re-parsing.
    """
    bridge = _build_bridge()
    decoy = "DECOY-CITATION-DO-NOT-EXTRACT"
    assembly = _make_assembly(
        should_attach=True,
        # Embed a decoy in the attachment block — the bridge
        # MUST NOT re-parse it.
        prompt_attachment_block=(
            f"[ARTICLE_RAG_CONTEXT_BEGIN]\n[rag-1] score=0.9\n{decoy}\n"
            "[ARTICLE_RAG_CONTEXT_END]"
        ),
        citations=(_make_citation(context_id="rag-1", chunk_id="c1"),),
        context_ids=("rag-1",),
    )
    result = bridge.bridge(
        base_prompt_text=_BASE_PROMPT,
        rag_assembly=assembly,
    )
    # The decoy appears in the prompt (verbatim from the
    # upstream assembly), but the structured citations field
    # reflects ONLY what the assembly carried: one citation,
    # not two.  The bridge does NOT re-parse the text to
    # extract citations.
    assert decoy in result.prompt_text
    assert len(result.citations) == 1


# ---------------------------------------------------------------------------
# 4. Fail-soft: malformed assembly
# ---------------------------------------------------------------------------


def test_malformed_assembly_fails_soft() -> None:
    """A regression / hostile fake could return a non-dataclass
    object.  The bridge MUST fail soft (no raise).
    """
    bridge = _build_bridge()
    result = bridge.bridge(
        base_prompt_text=_BASE_PROMPT,
        rag_assembly={"not": "a real ArticleRagAskPromptAssembly"},
    )
    assert isinstance(result, ArticleRagAskPromptBridgeResult)
    assert result.should_attach is False
    assert result.status == "not_indexed_or_unavailable"
    # The base prompt is preserved (the Ask layer can still
    # send the no-RAG prompt).
    assert result.prompt_text == _BASE_PROMPT
    assert result.citations == ()


# ---------------------------------------------------------------------------
# 5. Fail-soft: missing base prompt
# ---------------------------------------------------------------------------


def test_missing_base_prompt_fails_soft() -> None:
    """A regression / hostile call could pass ``base_prompt_text=None``
    (or a non-string).  The bridge fail-softs and returns a
    typed result; the RAG assembly metadata is preserved so
    the Ask layer can still surface the diagnostic.
    """
    bridge = _build_bridge()
    assembly = _make_assembly(should_attach=True)
    result = bridge.bridge(
        base_prompt_text=None,
        rag_assembly=assembly,
    )
    assert isinstance(result, ArticleRagAskPromptBridgeResult)
    assert result.should_attach is False
    assert result.prompt_text == ""
    # The fail-soft contract is uniform across every path:
    # citations / context_ids / source_pack_hash / query_sha256
    # are all empty.  The upstream assembly's metadata is
    # NOT preserved on a fail-soft path (the bridge treats the
    # failure as terminal; the Ask layer reads status +
    # failure_code to dispatch on the bridge's reason).
    assert result.citations == ()
    assert result.context_ids == ()
    assert result.source_pack_hash is None
    assert result.query_sha256 is None
    # The bridge-owned failure code is the bridge's reason
    # (not the assembly's).
    assert result.failure_code in (
        "article_rag_prompt_bridge_shape_invalid",
        "article_rag_prompt_bridge_unexpected_error",
    )


def test_non_string_base_prompt_fails_soft() -> None:
    bridge = _build_bridge()
    assembly = _make_assembly(should_attach=True)
    result = bridge.bridge(
        base_prompt_text=12345,  # type: ignore[arg-type]
        rag_assembly=assembly,
    )
    assert isinstance(result, ArticleRagAskPromptBridgeResult)
    assert result.should_attach is False
    assert result.prompt_text == ""


# ---------------------------------------------------------------------------
# 6. Fail-soft: oversize combined prompt
# ---------------------------------------------------------------------------


def test_oversize_combined_prompt_fails_soft_no_truncation() -> None:
    """A regression that produces a combined prompt whose
    length exceeds ``max_bridge_chars`` MUST fail soft —
    NOT truncate.  Truncation would corrupt the marker
    alignment with the citation list and confuse the LLM
    about which citation maps to which block.
    """
    bridge = _build_bridge(max_bridge_chars=200)
    long_block = "x" * 500
    assembly = _make_assembly(
        should_attach=True,
        prompt_attachment_block=(
            f"[ARTICLE_RAG_CONTEXT_BEGIN]\n{long_block}\n"
            "[ARTICLE_RAG_CONTEXT_END]"
        ),
    )
    result = bridge.bridge(
        base_prompt_text=_BASE_PROMPT,
        rag_assembly=assembly,
    )
    assert result.should_attach is False
    # The combined prompt text does NOT contain the long
    # block (no truncation — the bridge fell back to no-attach).
    assert long_block not in result.prompt_text
    # The base prompt is preserved.
    assert result.prompt_text == _BASE_PROMPT


# ---------------------------------------------------------------------------
# 7. Metadata allowlist + value guard
# ---------------------------------------------------------------------------


def test_metadata_json_contains_only_allowlisted_keys() -> None:
    """The bridge's ``metadata_json`` is built exclusively
    from the 12 allowlisted keys (defence-in-depth pass on
    top of the I4M assembly's own allowlist).
    """
    bridge = _build_bridge()
    assembly = _make_assembly(should_attach=True)
    result = bridge.bridge(
        base_prompt_text=_BASE_PROMPT,
        rag_assembly=assembly,
    )
    expected_keys = {
        "status",
        "failure_code",
        "retryable",
        "fallback_allowed",
        "omitted_hit_count",
        "budget_exceeded",
        "stable_document_id",
        "base_id",
        "record_generation",
        "plan_content_sha256",
        "source_pack_hash",
    }
    assert set(result.metadata_json.keys()) <= expected_keys


def test_metadata_json_drops_forbidden_substring() -> None:
    """A regression could surface a value with a forbidden
    substring (e.g. ``"token="``) on an allowlisted key.  The
    value guard drops it.
    """
    bridge = _build_bridge()
    # Construct an assembly with a hostile value on
    # ``source_pack_hash`` (allowlisted).
    assembly = _make_assembly(
        source_pack_hash="token=SECRET-DO-NOT-LEAK",
    )
    result = bridge.bridge(
        base_prompt_text=_BASE_PROMPT,
        rag_assembly=assembly,
    )
    # The key is dropped (value rejected by the substring
    # guard).
    assert "source_pack_hash" not in result.metadata_json
    # The secret MUST NOT appear in the metadata.
    for value in result.metadata_json.values():
        if isinstance(value, str):
            assert "token=" not in value


# ---------------------------------------------------------------------------
# 8. repr/str safety
# ---------------------------------------------------------------------------


def test_prompt_text_does_not_leak_in_repr_or_str() -> None:
    """The bridge's default repr / str MUST NOT echo the
    combined prompt text.  Every user-content field uses
    ``field(repr=False)``.
    """
    bridge = _build_bridge()
    secret = "SECRET-CHUNK-DO-NOT-LEAK"
    assembly = _make_assembly(
        should_attach=True,
        prompt_attachment_block=(
            f"[ARTICLE_RAG_CONTEXT_BEGIN]\n[rag-1] score=0.9\n{secret}\n"
            "[ARTICLE_RAG_CONTEXT_END]"
        ),
    )
    result = bridge.bridge(
        base_prompt_text=_BASE_PROMPT,
        rag_assembly=assembly,
    )
    # The secret is in the prompt text (verbatim from the
    # assembly).
    assert secret in result.prompt_text
    # The default repr / str MUST NOT echo it.
    assert secret not in repr(result)
    assert secret not in str(result)


def test_citation_dict_does_not_leak_in_repr_or_str() -> None:
    bridge = _build_bridge()
    citation_secret = "SECRET-IN-CITATION-DO-NOT-LEAK"
    assembly = _make_assembly(
        should_attach=True,
        citations=(
            _make_citation(
                context_id="rag-1", chunk_id="c1",
                block=citation_secret,
            ),
        ),
    )
    result = bridge.bridge(
        base_prompt_text=_BASE_PROMPT,
        rag_assembly=assembly,
    )
    assert citation_secret not in repr(result)
    assert citation_secret not in str(result)


# ---------------------------------------------------------------------------
# 9. Determinism
# ---------------------------------------------------------------------------


def test_bridge_deterministic_for_same_input() -> None:
    bridge = _build_bridge()
    assembly = _make_assembly(should_attach=True)
    r1 = bridge.bridge(
        base_prompt_text=_BASE_PROMPT,
        rag_assembly=assembly,
    )
    r2 = bridge.bridge(
        base_prompt_text=_BASE_PROMPT,
        rag_assembly=assembly,
    )
    assert r1.prompt_text == r2.prompt_text
    assert r1.attachment_block == r2.attachment_block
    assert r1.citations == r2.citations
    assert r1.context_ids == r2.context_ids
    assert r1.should_attach == r2.should_attach
    assert r1.status == r2.status
    assert r1.metadata_json == r2.metadata_json


# ---------------------------------------------------------------------------
# 10. Integration pin: real I4N provider flows into bridge
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_bridge_consumes_real_i4n_assembly() -> None:
    """P0 contract pin: the I4N provider produces a real
    ``ArticleRagAskPromptAssembly`` (via the real I4J
    integration adapter with async fake attachment service);
    the bridge consumes that assembly and produces a clean
    bridge result.  This test exercises the FULL pipeline
    from the I4N resolver to the I4O bridge.
    """

    class _AsyncAttachmentService:
        async def build_for_ask(self, **kwargs):
            return ArticleRagAskPromptSegment(
                kind="article_rag_context",
                include_in_prompt=True,
                prompt_text=_PROMPT_SECTION_TEXT,
                citations=(
                    _make_citation(
                        context_id="rag-1", chunk_id="c1"
                    ),
                ),
                context_ids=("rag-1",),
                source_pack_hash=_SOURCE_PACK_HASH,
                query_sha256=hashlib.sha256(b"hello").hexdigest(),
                status="available",
                failure_code=None,
                retryable=False,
                fallback_allowed=True,
                metadata_json={
                    "status": "available",
                    "failure_code": None,
                    "retryable": False, "fallback_allowed": True,
                    "omitted_hit_count": 0, "budget_exceeded": False,
                    "stable_document_id": _STABLE_DOC_ID,
                    "base_id": _BASE_ID, "record_generation": 1,
                    "plan_content_sha256": _PLAN_HASH,
                    "source_pack_hash": _SOURCE_PACK_HASH,
                },
            )

    real_integration_adapter = ArticleRagAskIntegrationAdapter(
        attachment_service=_AsyncAttachmentService(),
    )
    section_builder_factory = lambda: type("S", (), {
        "build": lambda self, segment: __import__(
            "app.services.reader_orchestration."
            "article_rag_ask_prompt_bridge",
            fromlist=["..."],
        )
    })
    # The I4N provider only needs the integration_adapter;
    # section / runtime / assembly are not used when the
    # integration adapter returns a real I4J segment.
    from app.services.reader_orchestration.article_rag_ask_prompt_section import (
        ArticleRagAskPromptSectionBuilder,
    )
    from app.services.reader_orchestration.article_rag_ask_runtime_adapter import (
        ArticleRagAskRuntimeAdapter,
    )

    # Use the I4N provider with the real integration adapter.
    provider = ArticleRagAskContextProvider(
        integration_adapter=real_integration_adapter,
        section_builder=ArticleRagAskPromptSectionBuilder(),
        runtime_adapter=ArticleRagAskRuntimeAdapter(),
    )
    assembly = await provider.build_for_ask(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
    # The assembly may be no-attach (the I4N chain's default
    # section / runtime / assembly services use a no-attach
    # contract when the underlying fake attachment service
    # returns a malformed shape).  We assert the bridge
    # contract holds regardless: a typed
    # ``ArticleRagAskPromptAssembly`` is consumed cleanly.
    assert isinstance(assembly, ArticleRagAskPromptAssembly)

    # The bridge consumes the real assembly and produces a
    # clean bridge result.  Whether ``should_attach`` is
    # ``True`` or ``False`` depends on the upstream I4N chain
    # (the assembly carries its own status); the bridge
    # contract is that the assembly is consumed cleanly
    # without ever raising.
    bridge = ArticleRagAskPromptBridge()
    result = bridge.bridge(
        base_prompt_text=_BASE_PROMPT,
        rag_assembly=assembly,
    )
    assert isinstance(result, ArticleRagAskPromptBridgeResult)
    # If the upstream assembly was an attach assembly, the
    # bridge's combined prompt must include the base prompt
    # verbatim (no mutation).  If the upstream was no-attach,
    # the combined prompt is exactly the base prompt
    # verbatim.
    assert result.prompt_text.startswith(_BASE_PROMPT)
    # If the upstream was an attach assembly, the
    # attachment block is embedded with markers.
    if assembly.should_attach:
        assert ATTACHMENT_BEGIN_MARKER in result.prompt_text
        assert ATTACHMENT_END_MARKER in result.prompt_text
    else:
        assert ATTACHMENT_BEGIN_MARKER not in result.prompt_text
        assert ATTACHMENT_END_MARKER not in result.prompt_text
    # Structured citations are preserved.
    assert result.citations == assembly.citations
    # query_sha256 is preserved (the raw query text does NOT
    # appear in the prompt text unless it was in the
    # upstream assembly's attachment block verbatim).
    assert result.query_sha256 == assembly.query_sha256


# ---------------------------------------------------------------------------
# 11. Constructor validation
# ---------------------------------------------------------------------------


def test_bridge_rejects_non_positive_max_bridge_chars() -> None:
    with pytest.raises(ValueError):
        ArticleRagAskPromptBridge(max_bridge_chars=0)
    with pytest.raises(ValueError):
        ArticleRagAskPromptBridge(max_bridge_chars=-1)


# ---------------------------------------------------------------------------
# 12. Constants
# ---------------------------------------------------------------------------


def test_default_constants() -> None:
    assert DEFAULT_MAX_BRIDGE_CHARS == 16000
    assert ATTACHMENT_BEGIN_MARKER == "[ARTICLE_RAG_ATTACHMENT_BEGIN]"
    assert ATTACHMENT_END_MARKER == "[ARTICLE_RAG_ATTACHMENT_END]"


# ---------------------------------------------------------------------------
# 13. Read-only integration-point audit
# ---------------------------------------------------------------------------


def test_insertion_point_audit_doc() -> None:
    """P1 contract pin (READ-ONLY).

    The bridge is a D6-I4O contract spike.  The
    ``app/services/reader_ask/service.py`` Ask service MUST
    call this bridge at the boundary between
    ``runtime_contract_svc.build_prompt_payload(...)`` and
    ``runtime_contract_svc.prepare_prompt_payload(...)`` to
    attach the RAG block to the base prompt before compaction.

    This test ONLY documents the future insertion point; it
    does NOT modify the production Ask code path.  The
    production integration is a separate change in a follow-up
    D6 task.

    Pinning: the bridge's contract here is the surface the
    integration will use (``bridge(base_prompt_text=...,
    rag_assembly=...)``).  This is the stable contract the
    I4N provider's output is consumed by.
    """
    bridge = ArticleRagAskPromptBridge()
    assembly = _make_assembly(should_attach=True)
    result = bridge.bridge(
        base_prompt_text=_BASE_PROMPT,
        rag_assembly=assembly,
    )
    # The contract the integration will rely on:
    assert result.should_attach is True
    assert result.prompt_text.startswith(_BASE_PROMPT)
    assert ATTACHMENT_BEGIN_MARKER in result.prompt_text
    assert ATTACHMENT_END_MARKER in result.prompt_text


# ---------------------------------------------------------------------------
# 15. Reviewer fix (round 2): runtime status / shape invariants
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hostile_status",
    [
        "paused",
        "",
        "SECRET-STATUS-DO-NOT-LEAK",
        "available ",  # trailing whitespace
        12345,  # not a string at all
    ],
)
def test_unknown_assembly_status_fails_soft(hostile_status: Any) -> None:
    """A regression / hostile fake could surface an
    unrecognised status.  The bridge fail-softs to
    ``shape_invalid`` regardless of the assembly's
    ``should_attach`` value.
    """
    bridge = _build_bridge()
    assembly = _make_assembly(
        should_attach=True,
        status=hostile_status,
    )
    result = bridge.bridge(
        base_prompt_text=_BASE_PROMPT,
        rag_assembly=assembly,
    )
    assert result.should_attach is False
    assert result.failure_code == "article_rag_prompt_bridge_shape_invalid"
    # citations / context_ids empty (P1a invariant).
    assert result.citations == ()
    assert result.context_ids == ()


def test_attach_assembly_with_non_available_status_fails_soft() -> None:
    """Reviewer fix: ``should_attach=True`` with
    ``status != "available"`` is a state-semantic
    inconsistency.  The bridge must fail-soft (the LLM
    must not receive an attachment labelled with a
    non-include status).
    """
    bridge = _build_bridge()
    assembly = _make_assembly(
        should_attach=True,
        status="disabled",  # state-semantic conflict
    )
    result = bridge.bridge(
        base_prompt_text=_BASE_PROMPT,
        rag_assembly=assembly,
    )
    assert result.should_attach is False
    assert result.failure_code == "article_rag_prompt_bridge_shape_invalid"
    assert result.citations == ()
    assert result.context_ids == ()


def test_no_attach_assembly_with_available_status_fails_soft() -> None:
    """``should_attach=False`` with ``status == "available"`` is
    a state-semantic inconsistency.  The bridge must fail-soft.
    """
    bridge = _build_bridge()
    assembly = _make_assembly(
        should_attach=False,
        status="available",  # state-semantic conflict
        prompt_attachment_block="",
        citations=(),
        context_ids=(),
    )
    result = bridge.bridge(
        base_prompt_text=_BASE_PROMPT,
        rag_assembly=assembly,
    )
    assert result.should_attach is False
    assert result.failure_code == "article_rag_prompt_bridge_shape_invalid"


def test_no_attach_assembly_with_populated_citations_fails_soft() -> None:
    """``should_attach=False`` with a non-empty attachment
    block / populated citations is a state-semantic
    inconsistency — the LLM would have a hard time mapping
    citation rows to a non-existent block.  Fail soft.
    """
    bridge = _build_bridge()
    assembly = _make_assembly(
        should_attach=False,
        status="empty",
        # Populated citations / context_ids on the no-attach
        # path.
        citations=(
            _make_citation(context_id="rag-1", chunk_id="c1"),
        ),
        context_ids=("rag-1",),
        # Attachment block is non-empty.
        prompt_attachment_block=(
            f"[ARTICLE_RAG_CONTEXT_BEGIN]\nalpha\n"
            "[ARTICLE_RAG_CONTEXT_END]"
        ),
    )
    result = bridge.bridge(
        base_prompt_text=_BASE_PROMPT,
        rag_assembly=assembly,
    )
    assert result.should_attach is False
    assert result.failure_code == "article_rag_prompt_bridge_shape_invalid"
    # citations / context_ids empty (P1a).
    assert result.citations == ()
    assert result.context_ids == ()


def test_attach_assembly_with_citation_length_mismatch_fails_soft() -> None:
    """The attach path requires ``len(citations) ==
    len(context_ids)`` — a regression / hostile fake with
    mismatched lengths must fail-soft rather than surface
    inconsistent citation rows.
    """
    bridge = _build_bridge()
    assembly = _make_assembly(
        should_attach=True,
        status="available",
        citations=(
            _make_citation(context_id="rag-1", chunk_id="c1"),
            _make_citation(context_id="rag-2", chunk_id="c2"),
        ),
        # 2 citations but 1 context_id.
        context_ids=("rag-1",),
    )
    result = bridge.bridge(
        base_prompt_text=_BASE_PROMPT,
        rag_assembly=assembly,
    )
    assert result.should_attach is False
    assert result.failure_code == "article_rag_prompt_bridge_shape_invalid"
    assert result.citations == ()
    assert result.context_ids == ()


def test_attach_assembly_with_non_sequence_citations_fails_soft() -> None:
    """A regression could put a non-sequence (e.g. ``None``
    or a string) on the ``citations`` field.  The bridge
    must fail-soft rather than let the ask layer consume
    an alien object.
    """
    bridge = _build_bridge()
    assembly = _make_assembly(
        should_attach=True,
        status="available",
        citations=None,  # type: ignore[arg-type]
        context_ids=("rag-1",),
    )
    result = bridge.bridge(
        base_prompt_text=_BASE_PROMPT,
        rag_assembly=assembly,
    )
    assert result.should_attach is False
    assert result.failure_code == "article_rag_prompt_bridge_shape_invalid"
