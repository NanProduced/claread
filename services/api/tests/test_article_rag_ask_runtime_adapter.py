# task-history: D6-I4L (renamed from test_d6_i4l_article_rag_ask_runtime_adapter.py)
"""D6-I4L: tests for Article RAG ask runtime boundary adapter.

Covers:
  * happy path: include_in_prompt=True → should_attach=True
    with verbatim ``prompt_section_text``.
  * no-attach path: include_in_prompt=False → should_attach=False
    with empty text and empty citations.
  * runtime status allowlist (5 values).
  * SHA-256 strict validation (64-char lowercase-hex).
  * shape mismatch on the attach path (citations vs context_ids).
  * oversized prompt_section_text → fail-soft (NO truncation).
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

from app.services.reader_orchestration.article_rag_ask_prompt_section import (
    ArticleRagAskPromptSection,
)
from app.services.reader_orchestration.article_rag_ask_runtime_adapter import (
    DEFAULT_MAX_RUNTIME_CHARS,
    FAILURE_CODE_RUNTIME_OVERSIZE,
    FAILURE_CODE_RUNTIME_SHAPE_INVALID,
    FAILURE_CODE_RUNTIME_STATUS_INVALID,
    FAILURE_CODE_RUNTIME_UNEXPECTED_ERROR,
    ArticleRagAskRuntimeAdapter,
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


def _make_section(
    *,
    include_in_prompt: bool = True,
    section_text: str = _PROMPT_SECTION_TEXT,
    citations: tuple[dict[str, Any], ...] | None = None,
    context_ids: tuple[str, ...] | None = None,
    source_pack_hash: str | None = _SOURCE_PACK_HASH,
    query_sha256: str | None = None,
    status: str = "available",
    failure_code: str | None = None,
    retryable: bool = False,
    fallback_allowed: bool = True,
    metadata_json: dict[str, Any] | None = None,
) -> ArticleRagAskPromptSection:
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
    return ArticleRagAskPromptSection(
        kind="article_rag_context",
        include_in_prompt=include_in_prompt,
        section_text=section_text,
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


def _build_adapter(
    *, max_runtime_chars: int = DEFAULT_MAX_RUNTIME_CHARS
) -> ArticleRagAskRuntimeAdapter:
    return ArticleRagAskRuntimeAdapter(
        max_runtime_chars=max_runtime_chars
    )


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------


def test_happy_path_attach_with_verbatim_section_text() -> None:
    adapter = _build_adapter()
    section = _make_section()
    ctx = adapter.build(section)
    assert isinstance(ctx, ArticleRagAskRuntimeContext)
    assert ctx.kind == "article_rag_context"
    assert ctx.should_attach is True
    # prompt_section_text MUST be verbatim from the section.
    assert ctx.prompt_section_text == section.section_text
    assert ctx.prompt_section_text == _PROMPT_SECTION_TEXT
    # Citations copied verbatim.
    assert ctx.citations == section.citations
    # Context ids copied verbatim.
    assert ctx.context_ids == section.context_ids
    # Diagnostic fields propagated.
    assert ctx.source_pack_hash == _SOURCE_PACK_HASH
    assert ctx.query_sha256 == hashlib.sha256(b"hello").hexdigest()
    assert ctx.status == "available"
    assert ctx.failure_code is None
    assert ctx.retryable is False
    assert ctx.fallback_allowed is True


# ---------------------------------------------------------------------------
# 2. No-attach path
# ---------------------------------------------------------------------------


def test_no_attach_path_empty_text() -> None:
    adapter = _build_adapter()
    section = _make_section(
        include_in_prompt=False,
        status="empty",
        section_text="",
        citations=(),
        context_ids=(),
        source_pack_hash=None,
        failure_code=None,
    )
    ctx = adapter.build(section)
    assert ctx.should_attach is False
    assert ctx.prompt_section_text == ""
    assert ctx.citations == ()
    assert ctx.context_ids == ()
    assert ctx.status == "empty"
    assert ctx.fallback_allowed is True


# ---------------------------------------------------------------------------
# 3. Citation not parsed from text
# ---------------------------------------------------------------------------


def test_citation_not_parsed_from_prompt_section_text() -> None:
    """The runtime adapter MUST NOT re-derive citations from
    ``prompt_section_text``.  Citations come from
    ``section.citations`` only."""
    decoy = "DECOY-CITATION-DO-NOT-EXTRACT"
    # A hostile fake that puts the decoy in the prompt body
    # but provides only one structured citation.
    section = _make_section(
        section_text=(
            f"[ARTICLE_RAG_CONTEXT_BEGIN]\n[rag-1] score=0.9\n{decoy}\n"
            "[ARTICLE_RAG_CONTEXT_END]"
        ),
        citations=(_make_citation(context_id="rag-1", chunk_id="c1"),),
        context_ids=("rag-1",),
    )
    adapter = _build_adapter()
    ctx = adapter.build(section)
    # The decoy may legitimately appear in the prompt body
    # (the I4K section copied verbatim from the upstream),
    # but the structured citations tuple reflects ONLY what
    # the section carried: one citation, not two.
    assert decoy in ctx.prompt_section_text
    assert len(ctx.citations) == 1


# ---------------------------------------------------------------------------
# 4. Runtime status allowlist
# ---------------------------------------------------------------------------


def test_paused_status_fails_soft() -> None:
    adapter = _build_adapter()
    section = _make_section(
        include_in_prompt=False,
        status="paused",
        section_text="",
        citations=(),
        context_ids=(),
    )
    ctx = adapter.build(section)
    assert ctx.status == "not_indexed_or_unavailable"
    assert ctx.failure_code == FAILURE_CODE_RUNTIME_UNEXPECTED_ERROR
    assert ctx.should_attach is False
    assert "paused" not in repr(ctx)
    assert "paused" not in str(ctx)


def test_empty_string_status_fails_soft() -> None:
    adapter = _build_adapter()
    section = _make_section(
        include_in_prompt=False,
        status="",
        section_text="",
        citations=(),
        context_ids=(),
    )
    ctx = adapter.build(section)
    assert ctx.status == "not_indexed_or_unavailable"
    assert ctx.failure_code == FAILURE_CODE_RUNTIME_UNEXPECTED_ERROR


def test_secret_bearing_status_does_not_leak() -> None:
    secret = "SECRET-STATUS-DO-NOT-LEAK"
    adapter = _build_adapter()
    section = _make_section(
        include_in_prompt=False,
        status=secret,
        section_text="",
        citations=(),
        context_ids=(),
    )
    ctx = adapter.build(section)
    assert ctx.status == "not_indexed_or_unavailable"
    assert ctx.failure_code == FAILURE_CODE_RUNTIME_UNEXPECTED_ERROR
    assert secret not in repr(ctx)
    assert secret not in str(ctx)


def test_all_five_allowed_statuses_round_trip() -> None:
    """All 5 I4H status values pass the runtime guard."""
    adapter = _build_adapter()

    # ``"available"`` is the only allowed status on the attach path.
    section = _make_section(
        include_in_prompt=True, status="available"
    )
    assert adapter.build(section).status == "available"

    # The other 4 are allowed on the no-attach path.
    for status in (
        "empty",
        "not_indexed_or_unavailable",
        "composer_rejected",
        "disabled",
    ):
        section = _make_section(
            include_in_prompt=False,
            status=status,
            section_text="",
            citations=(),
            context_ids=(),
        )
        ctx = adapter.build(section)
        assert ctx.status == status
        assert ctx.should_attach is False
        assert ctx.fallback_allowed is True


def test_attach_path_with_non_available_status_fails_soft() -> None:
    """The attach path requires ``status == "available"``."""
    adapter = _build_adapter()
    section = _make_section(
        include_in_prompt=True,
        status="disabled",
        section_text="[ARTICLE_RAG_CONTEXT_BEGIN]\nalpha\n[ARTICLE_RAG_CONTEXT_END]",
    )
    ctx = adapter.build(section)
    assert ctx.should_attach is False
    assert ctx.failure_code == FAILURE_CODE_RUNTIME_STATUS_INVALID


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
    """A regression / hostile fake in the upstream chain
    could put the raw query text on ``query_sha256``.  The
    runtime adapter MUST drop it (set to ``None``)."""
    adapter = _build_adapter()
    section = _make_section(query_sha256=raw_query_text)
    ctx = adapter.build(section)
    assert ctx.query_sha256 is None
    if "SECRET" in raw_query_text:
        assert raw_query_text not in repr(ctx)
        assert raw_query_text not in str(ctx)


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
    adapter = _build_adapter()
    section = _make_section(query_sha256=bad_hash)
    ctx = adapter.build(section)
    assert ctx.query_sha256 is None


def test_query_sha256_non_string_is_dropped() -> None:
    adapter = _build_adapter()
    for bad in (123, 3.14, True, ["hash"], {"hash": "x"}, b"bytes"):
        section = _make_section(query_sha256=bad)  # type: ignore[arg-type]
        ctx = adapter.build(section)
        assert ctx.query_sha256 is None


def test_query_sha256_valid_hex_passes_through() -> None:
    adapter = _build_adapter()
    valid_hash = "f" * 64
    section = _make_section(query_sha256=valid_hash)
    ctx = adapter.build(section)
    assert ctx.query_sha256 == valid_hash


# ---------------------------------------------------------------------------
# 6. Shape mismatch fail-soft
# ---------------------------------------------------------------------------


def test_attach_path_section_text_empty_fails_soft() -> None:
    """The attach path requires a non-empty
    ``prompt_section_text``.
    """
    adapter = _build_adapter()
    section = _make_section(
        include_in_prompt=True, section_text=""
    )
    ctx = adapter.build(section)
    assert ctx.should_attach is False
    assert ctx.failure_code == FAILURE_CODE_RUNTIME_SHAPE_INVALID


def test_attach_path_citation_context_id_length_mismatch_fails_soft() -> None:
    """The attach path requires ``len(citations) ==
    len(context_ids)``.
    """
    adapter = _build_adapter()
    section = _make_section(
        citations=(
            _make_citation(context_id="rag-1", chunk_id="c1"),
            _make_citation(context_id="rag-2", chunk_id="c2"),
        ),
        # Mismatch: 2 citations but 1 context_id.
        context_ids=("rag-1",),
    )
    ctx = adapter.build(section)
    assert ctx.should_attach is False
    assert ctx.failure_code == FAILURE_CODE_RUNTIME_SHAPE_INVALID
    assert ctx.prompt_section_text == ""


# ---------------------------------------------------------------------------
# 7. Oversized runtime text → fail-soft (NO truncation)
# ---------------------------------------------------------------------------


def test_oversized_prompt_section_text_fails_soft_no_truncation() -> None:
    """A regression that produces a ``prompt_section_text``
    whose length exceeds ``max_runtime_chars`` MUST fail soft
    — NOT truncate.  Truncation would corrupt the marker
    alignment with the citation list and confuse the LLM
    about which citation maps to which block.
    """
    adapter = _build_adapter(max_runtime_chars=200)
    long_text = "x" * 500  # well over the cap
    section = _make_section(section_text=long_text)
    ctx = adapter.build(section)
    assert ctx.should_attach is False
    assert ctx.prompt_section_text == ""
    assert ctx.failure_code == FAILURE_CODE_RUNTIME_OVERSIZE
    assert ctx.fallback_allowed is True
    # The long text MUST NOT appear truncated in
    # ``prompt_section_text`` (no truncation):
    assert long_text not in ctx.prompt_section_text


# ---------------------------------------------------------------------------
# 8. repr / str safety
# ---------------------------------------------------------------------------


def test_prompt_section_text_does_not_leak_in_repr_or_str() -> None:
    """The runtime context's default repr / str MUST NOT
    echo the prompt section text (chunk text / query
    fragments).
    """
    secret = "SECRET-CHUNK-DO-NOT-LEAK"
    section = _make_section(
        section_text=(
            f"[ARTICLE_RAG_CONTEXT_BEGIN]\n[rag-1] score=0.9\n{secret}\n"
            "[ARTICLE_RAG_CONTEXT_END]"
        )
    )
    ctx = _build_adapter().build(section)
    # The field itself is unchanged — the Ask runtime reads
    # ``prompt_section_text`` directly.
    assert secret in ctx.prompt_section_text
    # The default repr / str MUST NOT echo it.
    assert secret not in repr(ctx)
    assert secret not in str(ctx)


def test_citation_content_does_not_leak_in_repr_or_str() -> None:
    """Citation dicts are plan-backed content; they MUST NOT
    appear in default repr.
    """
    citation_secret = "SECRET-IN-CITATION-DO-NOT-LEAK"
    citation = _make_citation(
        context_id="rag-1", chunk_id="c1", block=citation_secret
    )
    section = _make_section(citations=(citation,))
    ctx = _build_adapter().build(section)
    assert citation_secret not in repr(ctx)
    assert citation_secret not in str(ctx)


def test_query_text_does_not_leak_in_repr_or_str() -> None:
    """Even though ``query_sha256`` is the only query-derived
    value on the segment, the underlying query MUST NOT
    surface anywhere.
    """
    secret = "SECRET-QUERY-DO-NOT-LEAK-DO-NOT-LEAK"
    # Construct a section with raw query in the prompt body
    # (would be allowed if upstream regression put it there)
    # AND a valid SHA-256 hash (so the hash survives the
    # scrub and ends up in ``query_sha256``).
    section = _make_section(
        section_text=(
            f"[ARTICLE_RAG_CONTEXT_BEGIN]\n[rag-1] score=0.9\n{secret}\n"
            "[ARTICLE_RAG_CONTEXT_END]"
        ),
        query_sha256=hashlib.sha256(secret.encode("utf-8")).hexdigest(),
    )
    ctx = _build_adapter().build(section)
    # Hash survives.
    assert ctx.query_sha256 == hashlib.sha256(secret.encode("utf-8")).hexdigest()
    # Hash itself doesn't echo the secret (SHA-256 is one-way).
    assert secret not in repr(ctx)
    assert secret not in str(ctx)


def test_top_level_source_pack_hash_with_secret_is_dropped() -> None:
    secret = "SECRET-TOPLEVEL-DO-NOT-LEAK"
    section = _make_section(source_pack_hash=f"token={secret}")
    ctx = _build_adapter().build(section)
    assert ctx.source_pack_hash is None
    assert secret not in repr(ctx)
    assert secret not in str(ctx)


def test_top_level_failure_code_with_secret_is_dropped() -> None:
    secret = "SECRET-FAILURE-DO-NOT-LEAK"
    section = _make_section(
        include_in_prompt=False,
        status="not_indexed_or_unavailable",
        failure_code=f"api_key={secret}",
        section_text="",
        citations=(),
        context_ids=(),
    )
    ctx = _build_adapter().build(section)
    assert ctx.failure_code is None
    assert secret not in repr(ctx)
    assert secret not in str(ctx)


# ---------------------------------------------------------------------------
# 9. Metadata allowlist + value guard
# ---------------------------------------------------------------------------


def test_metadata_json_contains_only_allowlisted_keys() -> None:
    adapter = _build_adapter()
    section = _make_section()
    ctx = adapter.build(section)
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
    assert set(ctx.metadata_json.keys()) <= expected_keys


def test_metadata_json_excludes_forbidden_keys() -> None:
    adapter = _build_adapter()
    section = _make_section()
    ctx = adapter.build(section)
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
        "prompt_section_text",
    ):
        assert forbidden not in ctx.metadata_json


def test_metadata_json_value_with_secret_substring_is_dropped() -> None:
    secret = "SECRET-METADATA-DO-NOT-LEAK"
    section = _make_section(
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
    ctx = _build_adapter().build(section)
    assert "source_pack_hash" not in ctx.metadata_json


# ---------------------------------------------------------------------------
# 10. Constructor validation
# ---------------------------------------------------------------------------


def test_adapter_rejects_non_positive_max_runtime_chars() -> None:
    with pytest.raises(ValueError):
        ArticleRagAskRuntimeAdapter(max_runtime_chars=0)
    with pytest.raises(ValueError):
        ArticleRagAskRuntimeAdapter(max_runtime_chars=-1)


# ---------------------------------------------------------------------------
# 11. Determinism
# ---------------------------------------------------------------------------


def test_runtime_context_deterministic_for_same_input() -> None:
    section = _make_section()

    def _run_once() -> ArticleRagAskRuntimeContext:
        return _build_adapter().build(section)

    a = _run_once()
    b = _run_once()
    assert a.prompt_section_text == b.prompt_section_text
    assert a.citations == b.citations
    assert a.context_ids == b.context_ids
    assert a.source_pack_hash == b.source_pack_hash
    assert a.query_sha256 == b.query_sha256
    assert a.status == b.status
    assert a.metadata_json == b.metadata_json
    assert a.should_attach == b.should_attach
    assert a.kind == b.kind


# ---------------------------------------------------------------------------
# 12. Constants
# ---------------------------------------------------------------------------


def test_default_max_runtime_chars_constant() -> None:
    assert DEFAULT_MAX_RUNTIME_CHARS == 4000


def test_failure_codes_are_distinct() -> None:
    codes = {
        FAILURE_CODE_RUNTIME_UNEXPECTED_ERROR,
        FAILURE_CODE_RUNTIME_OVERSIZE,
        FAILURE_CODE_RUNTIME_SHAPE_INVALID,
        FAILURE_CODE_RUNTIME_STATUS_INVALID,
    }
    assert len(codes) == 4


# ---------------------------------------------------------------------------
# 13. Malformed section object fail-soft
# ---------------------------------------------------------------------------


def test_malformed_section_object_fails_soft_unexpected() -> None:
    """A regression / hostile fake could return a non-dataclass
    object.  The runtime adapter MUST fail soft rather than
    crash.
    """
    adapter = _build_adapter()
    ctx = adapter.build({"kind": "article_rag_context"})
    assert ctx.should_attach is False
    assert ctx.status == "not_indexed_or_unavailable"
    assert ctx.failure_code == FAILURE_CODE_RUNTIME_UNEXPECTED_ERROR
    assert ctx.fallback_allowed is True


def test_real_section_with_malformed_fields_fails_soft_shape_invalid() -> None:
    """A real ``ArticleRagAskPromptSection`` (or duck-typed
    equivalent) with malformed fields (e.g. ``citations=None``)
    is a shape mismatch — fail-soft with
    ``FAILURE_CODE_RUNTIME_SHAPE_INVALID``.
    """
    adapter = _build_adapter()
    section = _make_section(
        include_in_prompt=True,
        # ``None`` is not a tuple / list — a shape mismatch.
        citations=None,  # type: ignore[arg-type]
        context_ids=("rag-1",),
    )
    ctx = adapter.build(section)
    assert ctx.should_attach is False
    assert ctx.failure_code == FAILURE_CODE_RUNTIME_SHAPE_INVALID


# ---------------------------------------------------------------------------
# 14. Reviewer fixes (round 1): malformed metadata + no-attach shape
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_metadata",
    [
        ["list", "not", "dict"],
        ("tuple", "not", "dict"),
        "string-not-dict",
        123,
        3.14,
        True,
        ["nested", {"k": "v"}],
    ],
)
def test_attach_path_does_not_raise_on_malformed_metadata(
    bad_metadata: Any,
) -> None:
    """Reviewer P1 fix: a regression / hostile fake in the
    upstream chain could surface a non-dict ``metadata_json``.
    A previous implementation called ``dict(metadata or {})``
    which raised ``TypeError`` on list / tuple / string /
    non-dict input and bypassed the fail-soft contract.

    The runtime adapter MUST NOT raise on any non-dict
    ``metadata_json``.  ``_scrub_metadata`` handles non-dict
    input by returning ``{}``, and the attach path proceeds
    with ``metadata_json={}`` (or fail-softs on other shape
    issues unrelated to metadata_json).
    """
    adapter = _build_adapter()
    # The shape is built with defaults (everything valid
    # EXCEPT ``metadata_json``); only the metadata_json is
    # malicious.  We assert that ``adapter.build(section)``
    # returns WITHOUT raising, regardless of which path /
    # status / failure_code the runtime adapter takes.
    section = _make_section(metadata_json=bad_metadata)  # type: ignore[arg-type]
    # MUST NOT raise.  The return MUST be a typed context.
    ctx = adapter.build(section)
    assert isinstance(ctx, ArticleRagAskRuntimeContext)
    # The metadata is scrubbed to ``{}``.
    assert ctx.metadata_json == {}


def test_attach_path_does_not_raise_when_metadata_is_list() -> None:
    """Specific check: a hostile fake returning
    ``metadata_json=["a", "b"]`` MUST NOT raise.  ``_scrub_metadata``
    handles the list by returning ``{}``; the attach path
    proceeds (subject to its own shape checks).
    """
    adapter = _build_adapter()
    section = _make_section(metadata_json=["a", "b"])  # type: ignore[arg-type]
    ctx = adapter.build(section)
    assert isinstance(ctx, ArticleRagAskRuntimeContext)
    assert ctx.metadata_json == {}


def test_no_attach_path_does_not_raise_on_malformed_metadata() -> None:
    """Reviewer P1 fix applied to the no-attach path too —
    a hostile non-dict ``metadata_json`` on the no-attach
    path MUST NOT raise.
    """
    adapter = _build_adapter()
    section = _make_section(
        include_in_prompt=False,
        status="empty",
        section_text="",
        citations=(),
        context_ids=(),
        metadata_json=["hostile", "list"],  # type: ignore[arg-type]
    )
    ctx = adapter.build(section)
    assert ctx.status == "empty"
    assert ctx.should_attach is False
    assert ctx.metadata_json == {}


def test_no_attach_path_with_non_empty_section_text_fails_soft() -> None:
    """Reviewer P2 fix: the no-attach path requires
    ``section_text == ""``.  A regression / hostile fake
    could surface ``include_in_prompt=False`` with
    non-empty text — fail soft.
    """
    adapter = _build_adapter()
    section = _make_section(
        include_in_prompt=False,
        status="not_indexed_or_unavailable",
        # Stray non-empty text on the no-attach path.
        section_text="stray text",
        citations=(),
        context_ids=(),
    )
    ctx = adapter.build(section)
    assert ctx.should_attach is False
    assert ctx.failure_code == FAILURE_CODE_RUNTIME_SHAPE_INVALID


def test_no_attach_path_with_non_empty_citations_fails_soft() -> None:
    """Reviewer P2 fix: the no-attach path requires
    ``len(citations) == 0``.
    """
    adapter = _build_adapter()
    section = _make_section(
        include_in_prompt=False,
        status="empty",
        section_text="",
        # Stray citation on the no-attach path.
        citations=(_make_citation(context_id="rag-1", chunk_id="c1"),),
        context_ids=("rag-1",),
    )
    ctx = adapter.build(section)
    assert ctx.should_attach is False
    assert ctx.failure_code == FAILURE_CODE_RUNTIME_SHAPE_INVALID


def test_no_attach_path_with_status_available_fails_soft() -> None:
    """Reviewer P2 fix: state-semantic consistency.  The
    no-attach path requires ``status != "available"`` —
    otherwise the upstream said "attach" but the section
    builder said "don't attach".  Fail soft.
    """
    adapter = _build_adapter()
    section = _make_section(
        include_in_prompt=False,
        status="available",  # state-semantic conflict
        section_text="",
        citations=(),
        context_ids=(),
    )
    ctx = adapter.build(section)
    assert ctx.should_attach is False
    assert ctx.failure_code == FAILURE_CODE_RUNTIME_SHAPE_INVALID