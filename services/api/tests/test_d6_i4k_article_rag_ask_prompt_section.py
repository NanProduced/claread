"""D6-I4K: tests for Article RAG ask prompt section builder.

Covers:
  * happy path: include_in_prompt=True → section_text wrapped in
    fixed markers; citations / context_ids / source_pack_hash
    copied verbatim; status / failure_code / retryable /
    fallback_allowed / query_sha256 propagated.
  * no-context path: include_in_prompt=False; section_text="";
    citations=(); context_ids=(); safe diagnostic fields preserved.
  * verbatim prompt body — the inner ``prompt_text`` is
    preserved exactly (no mutation / no truncation / no citation
    extraction).
  * citations are NOT parsed from the prompt text — even when
    the prompt embeds a citation-like decoy string, the
    ``citations`` tuple reflects what the segment carried.
  * citation / context_id length mismatch → fail-soft
    (FAILURE_CODE_SECTION_SHAPE_INVALID).
  * oversized section_text → fail-soft
    (FAILURE_CODE_SECTION_OVERSIZE); NO truncation.
  * malformed segment (wrong type) → fail-soft
    (FAILURE_CODE_SECTION_UNEXPECTED_ERROR).
  * include-path with missing / empty prompt_text → fail-soft.
  * repr / str safety: query_text / chunk text / secrets NEVER
    appear in ``repr(section)`` / ``str(section)``.
  * metadata allowlist + value guard — a hostile value on
    ``source_pack_hash`` / ``failure_code`` is dropped before
    reaching the section.
  * no DB / network / LLM.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from typing import Any

import pytest

from app.services.reader_orchestration.article_rag_ask_integration_adapter import (
    ArticleRagAskPromptSegment,
)
from app.services.reader_orchestration.article_rag_ask_prompt_section import (
    DEFAULT_MAX_SECTION_CHARS,
    FAILURE_CODE_SECTION_OVERSIZE,
    FAILURE_CODE_SECTION_SHAPE_INVALID,
    FAILURE_CODE_SECTION_UNEXPECTED_ERROR,
    SECTION_KIND,
    ArticleRagAskPromptSection,
    ArticleRagAskPromptSectionBuilder,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_RECORD_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
_STABLE_DOC_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
_BASE_ID = uuid.UUID("44444444-4444-4444-4444-444444444444")
_PLAN_HASH = "abc123def456" + "f" * 52
_SOURCE_PACK_HASH = "deadbeef" + "0" * 56
_PROMPT_TEXT = (
    "[rag-1] rank=1 score=0.950000\n"
    "alpha content\n\n"
    "[rag-2] rank=2 score=0.850000\n"
    "beta content"
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


def _make_segment(
    *,
    include_in_prompt: bool = True,
    prompt_text: str = _PROMPT_TEXT,
    citations: tuple[dict[str, Any], ...] | None = None,
    context_ids: tuple[str, ...] | None = None,
    source_pack_hash: str | None = _SOURCE_PACK_HASH,
    query_sha256: str | None = None,
    status: str = "available",
    failure_code: str | None = None,
    retryable: bool = False,
    fallback_allowed: bool = True,
    metadata_json: dict[str, Any] | None = None,
) -> ArticleRagAskPromptSegment:
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
            "index_version": "article_rag_index_v1",
            "source_pack_hash": source_pack_hash,
        }
    return ArticleRagAskPromptSegment(
        kind="article_rag_context",
        include_in_prompt=include_in_prompt,
        prompt_text=prompt_text,
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


def _build_builder(
    *, max_section_chars: int = DEFAULT_MAX_SECTION_CHARS
) -> ArticleRagAskPromptSectionBuilder:
    return ArticleRagAskPromptSectionBuilder(
        max_section_chars=max_section_chars
    )


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------


def test_happy_path_section_includes_markers_and_verbatim_prompt() -> None:
    builder = _build_builder()
    section = builder.build(_make_segment())
    assert isinstance(section, ArticleRagAskPromptSection)
    assert section.kind == "article_rag_context"
    assert section.include_in_prompt is True
    # The prompt body is verbatim — wrapped in fixed markers
    # but the inner content is unchanged.
    assert section.section_text == (
        "[ARTICLE_RAG_CONTEXT_BEGIN]\n"
        f"{_PROMPT_TEXT}\n"
        "[ARTICLE_RAG_CONTEXT_END]"
    )
    # The markers MUST be present in order.
    assert section.section_text.index("[ARTICLE_RAG_CONTEXT_BEGIN]") < (
        section.section_text.index("[ARTICLE_RAG_CONTEXT_END]")
    )
    # Citations are copied verbatim.
    assert section.citations == _make_segment().citations
    assert section.context_ids == ("rag-1", "rag-2")
    # Status / failure_code / retryable / fallback_allowed /
    # query_sha256 propagated.
    assert section.status == "available"
    assert section.failure_code is None
    assert section.retryable is False
    assert section.fallback_allowed is True
    assert section.source_pack_hash == _SOURCE_PACK_HASH
    assert section.query_sha256 == hashlib.sha256(b"hello").hexdigest()


# ---------------------------------------------------------------------------
# 2. Verbatim prompt body — no mutation
# ---------------------------------------------------------------------------


def test_prompt_body_preserved_verbatim() -> None:
    """The inner ``prompt_text`` MUST be preserved verbatim.  No
    re-parse, no re-order, no extra whitespace, no truncation."""
    raw_text = (
        "[rag-1] rank=1 score=0.950000\n"
        "alpha\n\n"
        "[rag-2] rank=2 score=0.850000\n"
        "beta"
    )
    builder = _build_builder()
    section = builder.build(_make_segment(prompt_text=raw_text))
    # The inner content is the exact raw_text.
    assert raw_text in section.section_text
    # The order of blocks is preserved (alpha before beta).
    assert section.section_text.index("alpha") < section.section_text.index(
        "beta"
    )


def test_citation_not_parsed_from_prompt_text() -> None:
    """A decoy citation-like string in ``prompt_text`` MUST NOT
    be extracted into the structured ``citations`` tuple.  The
    builder only takes ``citations`` from the segment.
    """
    decoy = "DECOY-CITATION-DO-NOT-EXTRACT"
    builder = _build_builder()
    section = builder.build(
        _make_segment(
            prompt_text=(
                f"[rag-1] rank=1 score=0.9\n{decoy}\n\n[rag-2] ..."
            ),
            # The segment's citations are intentionally
            # minimal — only the decoy must NOT appear in them.
            citations=(
                _make_citation(context_id="rag-1", chunk_id="c1"),
            ),
            context_ids=("rag-1",),
        )
    )
    assert section.context_ids == ("rag-1",)
    # The decoy appears in the prompt text (legitimate — the
    # composer's output is verbatim), but the structured
    # ``citations`` tuple reflects what the segment carried
    # (one citation, not two).
    assert decoy in section.section_text
    assert len(section.citations) == 1


# ---------------------------------------------------------------------------
# 3. No-context path
# ---------------------------------------------------------------------------


def test_no_context_path_empty_section() -> None:
    builder = _build_builder()
    section = builder.build(
        _make_segment(
            include_in_prompt=False,
            status="empty",
            prompt_text="",
            citations=(),
            context_ids=(),
            source_pack_hash=None,
            failure_code=None,
            metadata_json={
                "status": "empty",
                "failure_code": None,
                "retryable": False,
                "fallback_allowed": True,
                "omitted_hit_count": 0,
                "budget_exceeded": False,
                "stable_document_id": _STABLE_DOC_ID,
                "base_id": _BASE_ID,
                "record_generation": 1,
                "plan_content_sha256": _PLAN_HASH,
                "index_version": "article_rag_index_v1",
                "source_pack_hash": None,
            },
        )
    )
    assert section.include_in_prompt is False
    assert section.section_text == ""
    assert section.citations == ()
    assert section.context_ids == ()
    assert section.status == "empty"
    assert section.fallback_allowed is True
    # Stable ids surface in metadata_json.
    assert str(section.metadata_json["stable_document_id"]) == str(
        _STABLE_DOC_ID
    )


# ---------------------------------------------------------------------------
# 4. Fail-soft: shape mismatch
# ---------------------------------------------------------------------------


def test_citation_context_id_length_mismatch_fails_soft() -> None:
    """A regression / hostile fake could surface
    ``len(citations) != len(context_ids)``.  This is a
    contract violation — fail soft to
    ``FAILURE_CODE_SECTION_SHAPE_INVALID`` rather than produce
    a section whose markers don't map to its citations.
    """
    builder = _build_builder()
    section = builder.build(
        _make_segment(
            citations=(
                _make_citation(context_id="rag-1", chunk_id="c1"),
                _make_citation(context_id="rag-2", chunk_id="c2"),
            ),
            # Mismatch: 2 citations but 1 context_id.
            context_ids=("rag-1",),
        )
    )
    assert section.include_in_prompt is False
    assert section.section_text == ""
    assert section.status == "not_indexed_or_unavailable"
    assert section.failure_code == FAILURE_CODE_SECTION_SHAPE_INVALID
    assert section.fallback_allowed is True


def test_citation_not_iterable_fails_soft() -> None:
    """A regression that surfaces ``citations`` as a non-iterable
    (e.g. ``None``) must fail soft with
    ``FAILURE_CODE_SECTION_SHAPE_INVALID`` — the upstream chain
    promised a sequence and broke the contract.
    """
    builder = _build_builder()
    # Build a real ``ArticleRagAskPromptSegment`` (the shape
    # check at the top of ``build`` passes for real dataclass
    # instances) but with ``citations=None`` to simulate a
    # regression in the upstream chain.
    segment = _make_segment(
        include_in_prompt=True,
        prompt_text="alpha",
        # ``None`` is not a tuple / list — a shape mismatch.
        citations=None,  # type: ignore[arg-type]
        context_ids=("rag-1",),
    )
    section = builder.build(segment)
    assert section.include_in_prompt is False
    assert section.section_text == ""
    assert section.failure_code == FAILURE_CODE_SECTION_SHAPE_INVALID


def test_include_path_empty_prompt_text_fails_soft() -> None:
    """A hostile fake could surface
    ``include_in_prompt=True`` with ``prompt_text=""``.
    The builder MUST NOT silently produce a section with
    empty inner content — fail soft.
    """
    builder = _build_builder()
    section = builder.build(
        _make_segment(prompt_text="", include_in_prompt=True)
    )
    assert section.include_in_prompt is False
    assert section.section_text == ""
    assert section.status == "not_indexed_or_unavailable"
    assert section.failure_code == FAILURE_CODE_SECTION_SHAPE_INVALID


def test_malformed_segment_object_fails_soft() -> None:
    """A regression / hostile fake could return a non-dataclass
    object.  The builder MUST fail soft rather than crash.
    """
    builder = _build_builder()
    section = builder.build({"kind": "article_rag_context"})
    assert section.include_in_prompt is False
    assert section.status == "not_indexed_or_unavailable"
    assert section.failure_code == FAILURE_CODE_SECTION_UNEXPECTED_ERROR
    assert section.fallback_allowed is True


def test_dataclass_with_malformed_fields_fails_soft_shape_invalid() -> None:
    """A regression that returns a proper
    :class:`ArticleRagAskPromptSegment` (or a duck-typed
    equivalent) but with malformed fields (e.g. ``citations=None``)
    is a shape mismatch — fail-soft with
    ``FAILURE_CODE_SECTION_SHAPE_INVALID``.

    This is distinguished from the non-dataclass case above:
    the former is "wrong type" (unexpected), the latter is
    "right type, wrong shape" (shape invalid).
    """
    builder = _build_builder()
    segment = _make_segment(
        include_in_prompt=True,
        prompt_text="alpha",
        # ``None`` is not a tuple / list — a shape mismatch.
        citations=None,  # type: ignore[arg-type]
        context_ids=("rag-1",),
    )
    section = builder.build(segment)
    assert section.include_in_prompt is False
    assert section.failure_code == FAILURE_CODE_SECTION_SHAPE_INVALID


# ---------------------------------------------------------------------------
# 5. Fail-soft: oversized section (no truncation)
# ---------------------------------------------------------------------------


def test_oversized_section_text_fails_soft_no_truncation() -> None:
    """A regression that produces a prompt text whose wrapped
    section exceeds ``max_section_chars`` MUST fail soft — NOT
    truncate.  Truncation would corrupt the marker alignment
    with the citation list and confuse the LLM about which
    citation maps to which block.
    """
    # Construct a prompt whose wrapped section exceeds the
    # builder's cap.
    builder = _build_builder(max_section_chars=200)
    long_prompt = "x" * 500  # section text = 500 + markers + newlines > 200
    section = builder.build(
        _make_segment(prompt_text=long_prompt)
    )
    assert section.include_in_prompt is False
    assert section.section_text == ""
    assert section.status == "not_indexed_or_unavailable"
    assert section.failure_code == FAILURE_CODE_SECTION_OVERSIZE
    assert section.fallback_allowed is True
    # The long prompt MUST NOT appear in section_text (no
    # truncation — fail-soft is binary).
    assert long_prompt not in section.section_text


def test_oversized_with_default_cap_still_allows_normal_prompts() -> None:
    """Positive control: a normal-sized prompt fits the default
    cap and is NOT fail-softed.
    """
    builder = _build_builder()
    section = builder.build(_make_segment(prompt_text="alpha beta"))
    assert section.include_in_prompt is True
    assert "[ARTICLE_RAG_CONTEXT_BEGIN]" in section.section_text
    assert "[ARTICLE_RAG_CONTEXT_END]" in section.section_text


# ---------------------------------------------------------------------------
# 6. repr / str safety
# ---------------------------------------------------------------------------


def test_query_text_does_not_leak_in_repr_or_str() -> None:
    """The section's default repr / str MUST NOT echo the
    query_text or chunk text.  Every user-content field is
    ``field(repr=False)``.
    """
    secret = "SECRET-QUERY-DO-NOT-LEAK-DO-NOT-LEAK"
    builder = _build_builder()
    section = builder.build(
        _make_segment(
            prompt_text=f"[rag-1] score=0.9\n{secret}",
            query_sha256=hashlib.sha256(secret.encode("utf-8")).hexdigest(),
        )
    )
    assert secret not in repr(section)
    assert secret not in str(section)
    # The query_sha256 is the only query-derived value on the
    # section — but the hash itself doesn't echo the secret.
    assert secret.encode("utf-8").hex() not in repr(section)


def test_chunk_text_does_not_leak_in_repr_or_str() -> None:
    """The chunk text in the prompt body MUST NOT appear in the
    default dataclass repr / str.
    """
    chunk_secret = "SECRET-CHUNK-TEXT-DO-NOT-LEAK"
    builder = _build_builder()
    section = builder.build(
        _make_segment(
            prompt_text=f"[rag-1] score=0.9\n{chunk_secret}",
        )
    )
    assert chunk_secret not in repr(section)
    assert chunk_secret not in str(section)


def test_citation_content_does_not_leak_in_repr_or_str() -> None:
    """The structured citation dicts are user-content-bearing;
    they MUST NOT appear in the default repr.
    """
    citation_secret = "SECRET-CITATION-DO-NOT-LEAK"
    citation = _make_citation(
        context_id="rag-1", chunk_id="c1", block=citation_secret
    )
    builder = _build_builder()
    section = builder.build(
        _make_segment(citations=(citation,))
    )
    assert citation_secret not in repr(section)
    assert citation_secret not in str(section)


def test_metadata_json_value_with_secret_substring_is_dropped() -> None:
    """A regression that puts a secret on an allowlisted metadata
    key MUST be dropped by the value guard.
    """
    secret = "SECRET-METADATA-VALUE-DO-NOT-LEAK"
    builder = _build_builder()
    section = builder.build(
        _make_segment(
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
                "index_version": "article_rag_index_v1",
                # Hostile value: token-shaped.  Must be dropped.
                "source_pack_hash": f"token={secret}",
            },
        )
    )
    assert "source_pack_hash" not in section.metadata_json
    assert secret not in repr(section.metadata_json)


def test_top_level_source_pack_hash_with_secret_is_dropped() -> None:
    """A hostile ``source_pack_hash`` on the top-level field
    MUST also be dropped (not just from metadata_json).
    """
    secret = "SECRET-TOPLEVEL-DO-NOT-LEAK"
    builder = _build_builder()
    section = builder.build(
        _make_segment(source_pack_hash=f"token={secret}")
    )
    assert section.source_pack_hash is None
    assert secret not in repr(section)
    assert secret not in str(section)


def test_top_level_failure_code_with_secret_is_dropped() -> None:
    """A hostile ``failure_code`` on the top-level field MUST
    also be dropped (not just from metadata_json).
    """
    secret = "SECRET-FAILURE-DO-NOT-LEAK"
    builder = _build_builder()
    section = builder.build(
        _make_segment(failure_code=f"api_key={secret}")
    )
    assert section.failure_code is None
    assert secret not in repr(section)
    assert secret not in str(section)


# ---------------------------------------------------------------------------
# 7. Metadata allowlist + value guard
# ---------------------------------------------------------------------------


def test_metadata_json_contains_only_allowlisted_keys() -> None:
    """The section's metadata_json is built exclusively from the
    12 allowlisted keys.  A regression that surfaces a hostile
    key on the upstream segment MUST NOT leak through.
    """
    builder = _build_builder()
    section = builder.build(_make_segment())
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
        "index_version",
        "source_pack_hash",
    }
    assert set(section.metadata_json.keys()) <= expected_keys


def test_metadata_json_excludes_forbidden_keys() -> None:
    """The section's metadata_json MUST NOT contain:
      * ``query_text`` / ``provider_metadata`` / vector payload;
      * any UI projection key (plate / markdown / dom / slate /
        ui / render / html / text / chunks);
      * ``citations`` / ``prompt_text`` (top-level fields, not
        metadata).
    """
    builder = _build_builder()
    section = builder.build(_make_segment())
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
        "section_text",
    ):
        assert forbidden not in section.metadata_json


# ---------------------------------------------------------------------------
# 8. Constants / no-context behaviour
# ---------------------------------------------------------------------------


def test_default_max_section_chars_constant() -> None:
    assert DEFAULT_MAX_SECTION_CHARS == 4000


def test_section_kind_constant() -> None:
    assert SECTION_KIND == "article_rag_context"


def test_failure_code_constants_are_distinct() -> None:
    codes = {
        FAILURE_CODE_SECTION_UNEXPECTED_ERROR,
        FAILURE_CODE_SECTION_OVERSIZE,
        FAILURE_CODE_SECTION_SHAPE_INVALID,
    }
    assert len(codes) == 3


def test_no_context_section_preserves_stable_ids_in_metadata_json() -> None:
    """Stable ids MUST be echoed on the no-context path too —
    the Ask runtime uses them for cache keys and log dedup.
    """
    builder = _build_builder()
    section = builder.build(
        _make_segment(
            include_in_prompt=False,
            status="not_indexed_or_unavailable",
            prompt_text="",
            citations=(),
            context_ids=(),
            source_pack_hash=None,
            failure_code="context_no_indexed_run",
            metadata_json={
                "status": "not_indexed_or_unavailable",
                "failure_code": "context_no_indexed_run",
                "retryable": False,
                "fallback_allowed": True,
                "omitted_hit_count": 0,
                "budget_exceeded": False,
                "stable_document_id": _STABLE_DOC_ID,
                "base_id": _BASE_ID,
                "record_generation": 1,
                "plan_content_sha256": _PLAN_HASH,
                "index_version": "article_rag_index_v1",
                "source_pack_hash": None,
            },
        )
    )
    assert section.include_in_prompt is False
    assert str(section.metadata_json["stable_document_id"]) == str(
        _STABLE_DOC_ID
    )
    assert str(section.metadata_json["base_id"]) == str(_BASE_ID)
    assert section.metadata_json["record_generation"] == 1
    assert section.metadata_json["plan_content_sha256"] == _PLAN_HASH
    assert section.metadata_json["index_version"] == "article_rag_index_v1"
    assert section.metadata_json["failure_code"] == "context_no_indexed_run"


# ---------------------------------------------------------------------------
# 9. Constructor validation
# ---------------------------------------------------------------------------


def test_builder_rejects_non_positive_max_section_chars() -> None:
    with pytest.raises(ValueError):
        ArticleRagAskPromptSectionBuilder(max_section_chars=0)
    with pytest.raises(ValueError):
        ArticleRagAskPromptSectionBuilder(max_section_chars=-1)


# ---------------------------------------------------------------------------
# 10. Determinism
# ---------------------------------------------------------------------------


def test_section_deterministic_for_same_input() -> None:
    segment = _make_segment()

    def _run_once() -> ArticleRagAskPromptSection:
        return _build_builder().build(segment)

    a = _run_once()
    b = _run_once()
    assert a.section_text == b.section_text
    assert a.citations == b.citations
    assert a.context_ids == b.context_ids
    assert a.source_pack_hash == b.source_pack_hash
    assert a.query_sha256 == b.query_sha256
    assert a.status == b.status
    assert a.metadata_json == b.metadata_json
    assert a.include_in_prompt == b.include_in_prompt
    assert a.kind == b.kind


# ---------------------------------------------------------------------------
# 11. Reviewer fixes: _scrub_sha256 + runtime status allowlist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw_query_text",
    [
        "SECRET-QUERY-DO-NOT-LEAK-DO-NOT-LEAK",
        "hello world",
        "",
        "x",  # short string — too short to be SHA-256
    ],
)
def test_query_sha256_raw_query_text_is_dropped(raw_query_text: str) -> None:
    """Reviewer P1a fix: a regression / hostile fake in the
    upstream chain could put the raw query text on
    ``query_sha256``.  The dedicated ``_scrub_sha256`` helper
    MUST drop it (set to ``None``) so the secret does not
    surface as the top-level ``query_sha256`` field.
    """
    builder = _build_builder()
    section = builder.build(
        _make_segment(query_sha256=raw_query_text)
    )
    assert section.query_sha256 is None
    # The raw query MUST NOT appear in repr / str.  (Note: we
    # use a unique marker substring to avoid false positives
    # from unrelated fields that happen to contain common
    # letters like ``"x"`` or ``"hello"``.)
    if "SECRET" in raw_query_text:
        assert raw_query_text not in repr(section)
        assert raw_query_text not in str(section)


@pytest.mark.parametrize(
    "bad_hash",
    [
        "g" * 64,  # 'g' is not a hex char
        "A" * 64,  # uppercase letters are not allowed (we use lowercase)
        "abc",  # too short
        "x" * 64,  # wrong char
        "0" * 63,  # off by one
        "0" * 65,  # off by one
        "",  # empty
        "0" * 64 + "extra",  # too long
    ],
)
def test_query_sha256_malformed_hex_is_dropped(bad_hash: str) -> None:
    """Reviewer P1a fix: only a 64-char lowercase-hex string is
    a valid SHA-256 hex digest.  Anything else (uppercase, wrong
    chars, wrong length) is dropped.
    """
    builder = _build_builder()
    section = builder.build(_make_segment(query_sha256=bad_hash))
    assert section.query_sha256 is None


@pytest.mark.parametrize(
    "bad_value",
    [123, 3.14, True, ["hash"], {"hash": "x"}, b"bytes"],
)
def test_query_sha256_non_string_is_dropped(bad_value: Any) -> None:
    """Reviewer P1a fix: a non-string ``query_sha256`` is
    always dropped to ``None``.  (We don't parametrize on
    ``None`` itself because ``_make_segment`` falls back to a
    real SHA-256 hash when ``query_sha256`` is None — the
    helper has a deliberate default for that case.)
    """
    builder = _build_builder()
    section = builder.build(_make_segment(query_sha256=bad_value))
    assert section.query_sha256 is None


def test_query_sha256_valid_hex_passes_through() -> None:
    """Positive control: a well-formed 64-char lowercase-hex
    SHA-256 digest passes through unchanged.
    """
    builder = _build_builder()
    valid_hash = "f" * 64
    section = builder.build(_make_segment(query_sha256=valid_hash))
    assert section.query_sha256 == valid_hash


def test_paused_status_fails_soft_on_no_context_path() -> None:
    """Reviewer P1b fix: a regression / hostile fake in the
    upstream chain could surface an unrecognised status
    (``"paused"``) on the segment.  The runtime status
    allowlist fail-softs to ``not_indexed_or_unavailable`` so
    the Ask runtime's default branch still works.
    """
    builder = _build_builder()
    section = builder.build(
        _make_segment(
            include_in_prompt=False,
            status="paused",
            prompt_text="",
            citations=(),
            context_ids=(),
        )
    )
    assert section.status == "not_indexed_or_unavailable"
    assert section.failure_code == FAILURE_CODE_SECTION_UNEXPECTED_ERROR
    assert "paused" not in repr(section)
    assert "paused" not in str(section)


def test_empty_string_status_fails_soft() -> None:
    """An empty-string status is a contract violation — fail soft.
    """
    builder = _build_builder()
    section = builder.build(
        _make_segment(
            include_in_prompt=False,
            status="",
            prompt_text="",
            citations=(),
            context_ids=(),
        )
    )
    assert section.status == "not_indexed_or_unavailable"
    assert section.failure_code == FAILURE_CODE_SECTION_UNEXPECTED_ERROR


def test_secret_bearing_status_does_not_leak_in_repr() -> None:
    """A regression / hostile fake could put a secret value on
    the status field.  The runtime status allowlist fail-softs
    to ``not_indexed_or_unavailable`` AND the segment's status
    field is ``field(repr=False)`` so even if the status
    string slipped through, it would not appear in
    ``repr(section)`` / ``str(section)``.
    """
    secret = "SECRET-STATUS-DO-NOT-LEAK"
    builder = _build_builder()
    section = builder.build(
        _make_segment(
            include_in_prompt=False,
            status=secret,
            prompt_text="",
            citations=(),
            context_ids=(),
        )
    )
    # Fail-soft.
    assert section.status == "not_indexed_or_unavailable"
    assert section.failure_code == FAILURE_CODE_SECTION_UNEXPECTED_ERROR
    # The hostile status string MUST NOT appear anywhere on
    # the section.
    assert secret not in repr(section)
    assert secret not in str(section)


def test_include_path_with_non_available_status_fails_soft() -> None:
    """The include path requires ``status == "available"`` — a
    regression / hostile fake could surface
    ``include_in_prompt=True`` with a non-include status
    (``"disabled"``, ``"empty"``).  The include-path defence
    fail-softs with ``FAILURE_CODE_SECTION_SHAPE_INVALID``.
    """
    builder = _build_builder()
    section = builder.build(
        _make_segment(
            include_in_prompt=True,
            status="disabled",
            prompt_text="[rag-1] score=0.9\nalpha",
        )
    )
    assert section.include_in_prompt is False
    assert section.section_text == ""
    assert section.failure_code == FAILURE_CODE_SECTION_SHAPE_INVALID


def test_all_five_allowed_statuses_round_trip() -> None:
    """Positive control: all 5 I4H status values pass the
    runtime guard.  ``"available"`` is the only allowed status
    on the include path; the other 4 are allowed on the
    no-context path.
    """
    builder = _build_builder()

    # Available on the include path.
    section = builder.build(
        _make_segment(
            include_in_prompt=True,
            status="available",
            prompt_text="[rag-1] score=0.9\nalpha",
        )
    )
    assert section.status == "available"
    assert section.include_in_prompt is True

    # The other 4 on the no-context path.
    for status in ("empty", "not_indexed_or_unavailable", "composer_rejected", "disabled"):
        section = builder.build(
            _make_segment(
                include_in_prompt=False,
                status=status,
                prompt_text="",
                citations=(),
                context_ids=(),
            )
        )
        assert section.status == status
        assert section.include_in_prompt is False
        assert section.fallback_allowed is True