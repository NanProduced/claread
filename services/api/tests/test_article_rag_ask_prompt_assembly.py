# task-history: D6-I4M (renamed from test_d6_i4m_article_rag_ask_prompt_assembly.py)
"""Tests for the Article RAG ask prompt assembly boundary.

Covers:
  * happy path: should_attach=True with verbatim
    ``prompt_attachment_block``.
  * no-attach path: should_attach=False with empty block.
  * runtime status allowlist (5 values).
  * SHA-256 strict validation.
  * shape mismatch on the attach path (citations /
    context_ids).
  * oversized prompt_section_text → fail-soft (NO truncation).
  * malformed context object → fail-soft.
  * repr/str safety: query_text / chunk text / secrets NEVER
    appear in default repr.
  * metadata allowlist + value guard.
  * no DB / network / LLM.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

import pytest

from app.services.reader_orchestration.article_rag_ask_prompt_assembly import (
    DEFAULT_MAX_BLOCK_CHARS,
    FAILURE_CODE_ASSEMBLY_OVERSIZE,
    FAILURE_CODE_ASSEMBLY_SHAPE_INVALID,
    FAILURE_CODE_ASSEMBLY_STATUS_INVALID,
    FAILURE_CODE_ASSEMBLY_UNEXPECTED_ERROR,
    ArticleRagAskPromptAssembly,
    ArticleRagAskPromptAssemblyService,
)
from app.services.reader_orchestration.article_rag_ask_runtime_adapter import (
    ArticleRagAskRuntimeContext,
)

pytestmark = [
    pytest.mark.chain_article_rag,
    pytest.mark.seam_pure_unit,
    pytest.mark.life_permanent_regression,
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_STABLE_DOC_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
_BASE_ID = uuid.UUID("44444444-4444-4444-4444-444444444444")
_PLAN_HASH = "abc123def456" + "f" * 52
_SOURCE_PACK_HASH = "deadbeef" + "0" * 56
_PROMPT_SECTION_TEXT = (
    "[ARTICLE_RAG_CONTEXT_BEGIN]\n"
    "[rag-1] rank=1 score=0.950000\n"
    "alpha content\n\n"
    "[rag-2] rank=2 score=0.850000\n"
    "beta content\n"
    "[ARTICLE_RAG_CONTEXT_END]"
)


def _make_citation(
    *, context_id: str, chunk_id: str, block: str = "block-x"
) -> dict[str, Any]:
    return {
        "context_id": context_id,
        "chunk_id": chunk_id,
        "citation": {
            "reading_record_id": str(uuid.uuid4()),
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


def _make_context(
    *,
    should_attach: bool = True,
    prompt_section_text: str = _PROMPT_SECTION_TEXT,
    citations: tuple[dict[str, Any], ...] | None = None,
    context_ids: tuple[str, ...] | None = None,
    source_pack_hash: str | None = _SOURCE_PACK_HASH,
    query_sha256: str | None = None,
    status: str = "available",
    failure_code: str | None = None,
    retryable: bool = False,
    fallback_allowed: bool = True,
    metadata_json: dict[str, Any] | None = None,
) -> ArticleRagAskRuntimeContext:
    if citations is None:
        citations = (
            _make_citation(context_id="rag-1", chunk_id="c1"),
            _make_citation(context_id="rag-2", chunk_id="c2", block="block-y"),
        )
    if context_ids is None:
        context_ids = ("rag-1", "rag-2")
    if query_sha256 is None:
        query_sha256 = hashlib.sha256(b"hello").hexdigest()
    if metadata_json is None:
        metadata_json = {
            "status": status,
            "failure_code": failure_code,
            "retryable": retryable,
            "fallback_allowed": fallback_allowed,
            "omitted_hit_count": 0,
            "budget_exceeded": False,
            "stable_document_id": _STABLE_DOC_ID,
            "base_id": _BASE_ID,
            "record_generation": 1,
            "plan_content_sha256": _PLAN_HASH,
            "source_pack_hash": source_pack_hash,
        }
    return ArticleRagAskRuntimeContext(
        kind="article_rag_context",
        should_attach=should_attach,
        prompt_section_text=prompt_section_text,
        citations=citations,
        context_ids=context_ids,
        source_pack_hash=source_pack_hash,
        query_sha256=query_sha256,
        status=status,
        failure_code=failure_code,
        retryable=retryable,
        fallback_allowed=fallback_allowed,
        metadata_json=metadata_json,
    )


def _build_service(
    *, max_block_chars: int = DEFAULT_MAX_BLOCK_CHARS
) -> ArticleRagAskPromptAssemblyService:
    return ArticleRagAskPromptAssemblyService(
        max_block_chars=max_block_chars
    )


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------


def test_happy_path_attach_with_verbatim_block() -> None:
    service = _build_service()
    ctx = _make_context()
    assembly = service.assemble(ctx)
    assert isinstance(assembly, ArticleRagAskPromptAssembly)
    assert assembly.kind == "article_rag_context"
    assert assembly.should_attach is True
    # prompt_attachment_block MUST be verbatim from the context.
    assert assembly.prompt_attachment_block == _PROMPT_SECTION_TEXT
    assert assembly.prompt_attachment_block == ctx.prompt_section_text
    # Citations copied verbatim.
    assert assembly.citations == ctx.citations
    # Context ids copied verbatim.
    assert assembly.context_ids == ("rag-1", "rag-2")
    # Diagnostic fields propagated.
    assert assembly.source_pack_hash == _SOURCE_PACK_HASH
    assert assembly.query_sha256 == hashlib.sha256(b"hello").hexdigest()
    assert assembly.status == "available"
    assert assembly.failure_code is None
    assert assembly.retryable is False
    assert assembly.fallback_allowed is True


# ---------------------------------------------------------------------------
# 2. No-attach path
# ---------------------------------------------------------------------------


def test_no_attach_path_empty_block() -> None:
    service = _build_service()
    ctx = _make_context(
        should_attach=False,
        status="empty",
        prompt_section_text="",
        citations=(),
        context_ids=(),
        source_pack_hash=None,
        failure_code=None,
    )
    assembly = service.assemble(ctx)
    assert assembly.should_attach is False
    assert assembly.prompt_attachment_block == ""
    assert assembly.citations == ()
    assert assembly.context_ids == ()
    assert assembly.status == "empty"
    assert assembly.fallback_allowed is True


# ---------------------------------------------------------------------------
# 3. Citation not parsed from prompt_attachment_block
# ---------------------------------------------------------------------------


def test_citation_not_parsed_from_prompt_block() -> None:
    """The assembly service MUST NOT re-derive citations from
    ``prompt_attachment_block``.  Citations come from
    ``context.citations`` only."""
    decoy = "DECOY-CITATION-DO-NOT-EXTRACT"
    section = _make_context(
        prompt_section_text=(
            f"[ARTICLE_RAG_CONTEXT_BEGIN]\n[rag-1] score=0.9\n{decoy}\n"
            "[ARTICLE_RAG_CONTEXT_END]"
        ),
        citations=(_make_citation(context_id="rag-1", chunk_id="c1"),),
        context_ids=("rag-1",),
    )
    service = _build_service()
    assembly = service.assemble(section)
    # The decoy may legitimately appear in the block (the I4L
    # context copied verbatim from the upstream), but the
    # structured citations tuple reflects ONLY what the context
    # carried: one citation, not two.
    assert decoy in assembly.prompt_attachment_block
    assert len(assembly.citations) == 1


# ---------------------------------------------------------------------------
# 4. Runtime status allowlist
# ---------------------------------------------------------------------------


def test_paused_status_fails_soft() -> None:
    service = _build_service()
    ctx = _make_context(
        should_attach=False,
        status="paused",
        prompt_section_text="",
        citations=(),
        context_ids=(),
    )
    assembly = service.assemble(ctx)
    assert assembly.status == "not_indexed_or_unavailable"
    assert assembly.failure_code == FAILURE_CODE_ASSEMBLY_UNEXPECTED_ERROR
    assert assembly.should_attach is False
    assert "paused" not in repr(assembly)
    assert "paused" not in str(assembly)


def test_empty_string_status_fails_soft() -> None:
    service = _build_service()
    ctx = _make_context(
        should_attach=False,
        status="",
        prompt_section_text="",
        citations=(),
        context_ids=(),
    )
    assembly = service.assemble(ctx)
    assert assembly.status == "not_indexed_or_unavailable"
    assert assembly.failure_code == FAILURE_CODE_ASSEMBLY_UNEXPECTED_ERROR


def test_secret_bearing_status_does_not_leak() -> None:
    secret = "SECRET-STATUS-DO-NOT-LEAK"
    service = _build_service()
    ctx = _make_context(
        should_attach=False,
        status=secret,
        prompt_section_text="",
        citations=(),
        context_ids=(),
    )
    assembly = service.assemble(ctx)
    assert assembly.status == "not_indexed_or_unavailable"
    assert assembly.failure_code == FAILURE_CODE_ASSEMBLY_UNEXPECTED_ERROR
    assert secret not in repr(assembly)
    assert secret not in str(assembly)


def test_attach_path_with_non_available_status_fails_soft() -> None:
    """The attach path requires ``status == "available"``."""
    service = _build_service()
    ctx = _make_context(
        should_attach=True,
        status="disabled",
        prompt_section_text="[ARTICLE_RAG_CONTEXT_BEGIN]\nalpha\n[ARTICLE_RAG_CONTEXT_END]",
    )
    assembly = service.assemble(ctx)
    assert assembly.should_attach is False
    assert assembly.failure_code == FAILURE_CODE_ASSEMBLY_STATUS_INVALID


def test_all_five_allowed_statuses_round_trip() -> None:
    """All 5 I4H status values pass the runtime guard."""
    service = _build_service()

    # ``"available"`` is the only allowed status on the attach path.
    ctx = _make_context(should_attach=True, status="available")
    assert service.assemble(ctx).status == "available"

    # The other 4 are allowed on the no-attach path.
    for status in (
        "empty",
        "not_indexed_or_unavailable",
        "composer_rejected",
        "disabled",
    ):
        ctx = _make_context(
            should_attach=False,
            status=status,
            prompt_section_text="",
            citations=(),
            context_ids=(),
        )
        assembly = service.assemble(ctx)
        assert assembly.status == status
        assert assembly.should_attach is False
        assert assembly.fallback_allowed is True


# ---------------------------------------------------------------------------
# 5. SHA-256 strict validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw_query_text",
    [
        "SECRET-QUERY-DO-NOT-LEAK-DO-NOT-LEAK",
        "hello world",
        "",
        "x",
    ],
)
def test_query_sha256_raw_query_text_is_dropped(raw_query_text: str) -> None:
    """A regression / hostile fake in the upstream chain could
    put the raw query text on ``query_sha256``.  The assembly
    adapter MUST drop it (set to ``None``)."""
    service = _build_service()
    ctx = _make_context(query_sha256=raw_query_text)
    assembly = service.assemble(ctx)
    assert assembly.query_sha256 is None
    if "SECRET" in raw_query_text:
        assert raw_query_text not in repr(assembly)
        assert raw_query_text not in str(assembly)


@pytest.mark.parametrize(
    "bad_hash",
    [
        "g" * 64,
        "A" * 64,
        "abc",
        "x" * 64,
        "0" * 63,
        "0" * 65,
        "",
    ],
)
def test_query_sha256_malformed_hex_is_dropped(bad_hash: str) -> None:
    service = _build_service()
    ctx = _make_context(query_sha256=bad_hash)
    assembly = service.assemble(ctx)
    assert assembly.query_sha256 is None


def test_query_sha256_valid_hex_passes_through() -> None:
    service = _build_service()
    valid_hash = "f" * 64
    ctx = _make_context(query_sha256=valid_hash)
    assembly = service.assemble(ctx)
    assert assembly.query_sha256 == valid_hash


# ---------------------------------------------------------------------------
# 6. Shape mismatch fail-soft
# ---------------------------------------------------------------------------


def test_attach_path_empty_prompt_section_text_fails_soft() -> None:
    service = _build_service()
    ctx = _make_context(
        should_attach=True, prompt_section_text=""
    )
    assembly = service.assemble(ctx)
    assert assembly.should_attach is False
    assert assembly.failure_code == FAILURE_CODE_ASSEMBLY_SHAPE_INVALID


def test_attach_path_citation_context_id_length_mismatch_fails_soft() -> None:
    service = _build_service()
    ctx = _make_context(
        citations=(
            _make_citation(context_id="rag-1", chunk_id="c1"),
            _make_citation(context_id="rag-2", chunk_id="c2"),
        ),
        # Mismatch: 2 citations but 1 context_id.
        context_ids=("rag-1",),
    )
    assembly = service.assemble(ctx)
    assert assembly.should_attach is False
    assert assembly.failure_code == FAILURE_CODE_ASSEMBLY_SHAPE_INVALID
    assert assembly.prompt_attachment_block == ""


# ---------------------------------------------------------------------------
# 7. Oversized prompt_section_text → fail-soft (NO truncation)
# ---------------------------------------------------------------------------


def test_oversized_prompt_section_text_fails_soft_no_truncation() -> None:
    """A regression that produces a ``prompt_section_text``
    whose length exceeds ``max_block_chars`` MUST fail soft —
    NOT truncate.  Truncation would corrupt the marker
    alignment with the citation list and confuse the LLM
    about which citation maps to which block.
    """
    service = _build_service(max_block_chars=200)
    long_text = "x" * 500  # well over the cap
    ctx = _make_context(prompt_section_text=long_text)
    assembly = service.assemble(ctx)
    assert assembly.should_attach is False
    assert assembly.prompt_attachment_block == ""
    assert assembly.failure_code == FAILURE_CODE_ASSEMBLY_OVERSIZE
    assert assembly.fallback_allowed is True
    # The long text MUST NOT appear truncated in
    # ``prompt_attachment_block`` (no truncation):
    assert long_text not in assembly.prompt_attachment_block


# ---------------------------------------------------------------------------
# 8. Malformed context object fail-soft
# ---------------------------------------------------------------------------


def test_malformed_context_object_fails_soft_unexpected() -> None:
    """A regression / hostile fake could return a non-dataclass
    object.  The assembly service MUST fail soft rather than
    crash.
    """
    service = _build_service()
    assembly = service.assemble({"kind": "article_rag_context"})
    assert assembly.should_attach is False
    assert assembly.status == "not_indexed_or_unavailable"
    assert assembly.failure_code == FAILURE_CODE_ASSEMBLY_UNEXPECTED_ERROR
    assert assembly.fallback_allowed is True


def test_real_context_with_malformed_fields_fails_soft_shape_invalid() -> None:
    """A real ``ArticleRagAskRuntimeContext`` (or duck-typed
    equivalent) with malformed fields (e.g. ``citations=None``)
    is a shape mismatch — fail-soft with
    ``FAILURE_CODE_ASSEMBLY_SHAPE_INVALID``.
    """
    service = _build_service()
    ctx = _make_context(
        should_attach=True,
        # ``None`` is not a tuple / list — a shape mismatch.
        citations=None,  # type: ignore[arg-type]
        context_ids=("rag-1",),
    )
    assembly = service.assemble(ctx)
    assert assembly.should_attach is False
    assert assembly.failure_code == FAILURE_CODE_ASSEMBLY_SHAPE_INVALID


# ---------------------------------------------------------------------------
# 9. repr/str safety
# ---------------------------------------------------------------------------


def test_prompt_attachment_block_does_not_leak_in_repr_or_str() -> None:
    """The assembly's default repr / str MUST NOT echo the
    prompt attachment block (chunk text / query fragments).
    """
    secret = "SECRET-CHUNK-DO-NOT-LEAK"
    ctx = _make_context(
        prompt_section_text=(
            f"[ARTICLE_RAG_CONTEXT_BEGIN]\n[rag-1] score=0.9\n{secret}\n"
            "[ARTICLE_RAG_CONTEXT_END]"
        )
    )
    assembly = _build_service().assemble(ctx)
    # The field itself is unchanged.
    assert secret in assembly.prompt_attachment_block
    # The default repr / str MUST NOT echo it.
    assert secret not in repr(assembly)
    assert secret not in str(assembly)


def test_citation_content_does_not_leak_in_repr_or_str() -> None:
    """Citation dicts are plan-backed content; they MUST NOT
    appear in default repr.
    """
    citation_secret = "SECRET-IN-CITATION-DO-NOT-LEAK"
    citation = _make_citation(
        context_id="rag-1", chunk_id="c1", block=citation_secret
    )
    ctx = _make_context(citations=(citation,))
    assembly = _build_service().assemble(ctx)
    assert citation_secret not in repr(assembly)
    assert citation_secret not in str(assembly)


def test_query_text_does_not_leak_in_repr_or_str() -> None:
    """Even though ``query_sha256`` is the only query-derived
    value, the underlying query MUST NOT surface anywhere.
    """
    secret = "SECRET-QUERY-DO-NOT-LEAK-DO-NOT-LEAK"
    ctx = _make_context(
        prompt_section_text=(
            f"[ARTICLE_RAG_CONTEXT_BEGIN]\n[rag-1] score=0.9\n{secret}\n"
            "[ARTICLE_RAG_CONTEXT_END]"
        ),
        query_sha256=hashlib.sha256(secret.encode("utf-8")).hexdigest(),
    )
    assembly = _build_service().assemble(ctx)
    # Hash survives.
    assert assembly.query_sha256 == hashlib.sha256(
        secret.encode("utf-8")
    ).hexdigest()
    # Hash itself doesn't echo the secret (SHA-256 is one-way).
    assert secret not in repr(assembly)
    assert secret not in str(assembly)


def test_top_level_source_pack_hash_with_secret_is_dropped() -> None:
    secret = "SECRET-TOPLEVEL-DO-NOT-LEAK"
    ctx = _make_context(source_pack_hash=f"token={secret}")
    assembly = _build_service().assemble(ctx)
    assert assembly.source_pack_hash is None
    assert secret not in repr(assembly)
    assert secret not in str(assembly)


def test_top_level_failure_code_with_secret_is_dropped() -> None:
    secret = "SECRET-FAILURE-DO-NOT-LEAK"
    ctx = _make_context(
        should_attach=False,
        status="not_indexed_or_unavailable",
        failure_code=f"api_key={secret}",
        prompt_section_text="",
        citations=(),
        context_ids=(),
    )
    assembly = _build_service().assemble(ctx)
    assert assembly.failure_code is None
    assert secret not in repr(assembly)
    assert secret not in str(assembly)


# ---------------------------------------------------------------------------
# 10. Metadata allowlist + value guard
# ---------------------------------------------------------------------------


def test_metadata_json_contains_only_allowlisted_keys() -> None:
    service = _build_service()
    ctx = _make_context()
    assembly = service.assemble(ctx)
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
    assert set(assembly.metadata_json.keys()) <= expected_keys


def test_metadata_json_excludes_forbidden_keys() -> None:
    service = _build_service()
    ctx = _make_context()
    assembly = service.assemble(ctx)
    for forbidden in (
        "query_text",
        "query",
        "provider_metadata",
        "plate",
        "markdown",
        "dom",
        "slate",
        "ui",
        "render_profile",
        "html",
        "text",
        "chunks",
        "citations",
        "prompt_text",
        "prompt_attachment_block",
    ):
        assert forbidden not in assembly.metadata_json


def test_metadata_json_value_with_secret_substring_is_dropped() -> None:
    secret = "SECRET-METADATA-DO-NOT-LEAK"
    service = _build_service()
    ctx = _make_context(
        metadata_json={
            "status": "available",
            "failure_code": None,
            "retryable": False,
            "fallback_allowed": True,
            "omitted_hit_count": 0,
            "budget_exceeded": False,
            "stable_document_id": _STABLE_DOC_ID,
            "base_id": _BASE_ID,
            "record_generation": 1,
            "plan_content_sha256": _PLAN_HASH,
            # Hostile: token-shaped.
            "source_pack_hash": f"token={secret}",
        },
    )
    assembly = service.assemble(ctx)
    assert "source_pack_hash" not in assembly.metadata_json


# ---------------------------------------------------------------------------
# 11. Constructor validation
# ---------------------------------------------------------------------------


def test_service_rejects_non_positive_max_block_chars() -> None:
    with pytest.raises(ValueError):
        ArticleRagAskPromptAssemblyService(max_block_chars=0)
    with pytest.raises(ValueError):
        ArticleRagAskPromptAssemblyService(max_block_chars=-1)


# ---------------------------------------------------------------------------
# 12. Determinism
# ---------------------------------------------------------------------------


def test_assembly_deterministic_for_same_input() -> None:
    ctx = _make_context()

    def _run_once() -> ArticleRagAskPromptAssembly:
        return _build_service().assemble(ctx)

    a = _run_once()
    b = _run_once()
    assert a.prompt_attachment_block == b.prompt_attachment_block
    assert a.citations == b.citations
    assert a.context_ids == b.context_ids
    assert a.source_pack_hash == b.source_pack_hash
    assert a.query_sha256 == b.query_sha256
    assert a.status == b.status
    assert a.metadata_json == b.metadata_json
    assert a.should_attach == b.should_attach
    assert a.kind == b.kind


# ---------------------------------------------------------------------------
# 13. Constants
# ---------------------------------------------------------------------------


def test_default_max_block_chars_constant() -> None:
    assert DEFAULT_MAX_BLOCK_CHARS == 4000


def test_failure_codes_are_distinct() -> None:
    codes = {
        FAILURE_CODE_ASSEMBLY_UNEXPECTED_ERROR,
        FAILURE_CODE_ASSEMBLY_OVERSIZE,
        FAILURE_CODE_ASSEMBLY_SHAPE_INVALID,
        FAILURE_CODE_ASSEMBLY_STATUS_INVALID,
    }
    assert len(codes) == 4


# ---------------------------------------------------------------------------
# 14. No-attach path shape validation (mirror of I4L)
# ---------------------------------------------------------------------------


def test_no_attach_path_with_non_empty_prompt_fails_soft() -> None:
    service = _build_service()
    ctx = _make_context(
        should_attach=False,
        status="not_indexed_or_unavailable",
        # Stray non-empty text on the no-attach path.
        prompt_section_text="stray text",
        citations=(),
        context_ids=(),
    )
    assembly = service.assemble(ctx)
    assert assembly.should_attach is False
    assert assembly.failure_code == FAILURE_CODE_ASSEMBLY_SHAPE_INVALID


def test_no_attach_path_with_non_empty_citations_fails_soft() -> None:
    service = _build_service()
    ctx = _make_context(
        should_attach=False,
        status="empty",
        prompt_section_text="",
        # Stray citation on the no-attach path.
        citations=(_make_citation(context_id="rag-1", chunk_id="c1"),),
        context_ids=("rag-1",),
    )
    assembly = service.assemble(ctx)
    assert assembly.should_attach is False
    assert assembly.failure_code == FAILURE_CODE_ASSEMBLY_SHAPE_INVALID


def test_no_attach_path_with_status_available_fails_soft() -> None:
    """State-semantic consistency: the no-attach path requires
    ``status != "available"``.
    """
    service = _build_service()
    ctx = _make_context(
        should_attach=False,
        status="available",  # state-semantic conflict
        prompt_section_text="",
        citations=(),
        context_ids=(),
    )
    assembly = service.assemble(ctx)
    assert assembly.should_attach is False
    assert assembly.failure_code == FAILURE_CODE_ASSEMBLY_SHAPE_INVALID