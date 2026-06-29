"""D6-I4F: tests for Article RAG context pack service.

Covers:
  * happy path — hits → context items, fields complete, order stable.
  * ``query_text`` never appears on the result or in any error
    message; ``query_sha256`` is the only query-derived field.
  * zero hits → empty pack (``items=()``).
  * character budget — normal truncation, first oversized chunk
    included + ``budget_exceeded=True``.
  * metadata denylist scrub (defence in depth against future
    regressions in the retrieval path).
  * citation comes verbatim from the retrieval hit.
  * retrieval error → typed ``ArticleRagContextServiceError`` with
    ``__cause__`` preserved.
  * invalid ``limit`` / ``max_context_chars`` / blank ``query_text``
    fail closed.
  * ``context_id`` deterministic (``rag-1``, ``rag-2``, ...).

No real DB / Zilliz / DashScope / LLM.  The retrieval service is
replaced with a ``FakeRetrievalService``.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.services.reader_orchestration.article_rag_context_service import (
    DEFAULT_LIMIT,
    DEFAULT_MAX_CONTEXT_CHARS,
    ArticleRagContextItem,
    ArticleRagContextPack,
    ArticleRagContextService,
    ArticleRagContextServiceError,
    FAILURE_CODE_CONTEXT_EMPTY_QUERY,
    FAILURE_CODE_CONTEXT_INVALID_BUDGET,
    FAILURE_CODE_CONTEXT_INVALID_LIMIT,
    FAILURE_CODE_CONTEXT_RETRIEVAL_FAILED,
)
from app.services.reader_orchestration.article_rag_index_worker import (
    ArticleRagIndexWorkerError,
)
from app.services.reader_orchestration.article_rag_retrieval_service import (
    DEFAULT_INDEX_VERSION,
    MAX_RETRIEVAL_LIMIT,
    ArticleRagRetrievalHit,
    ArticleRagRetrievalResult,
    ArticleRagRetrievalServiceError,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_RECORD_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
_USER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
_STABLE_DOC_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
_BASE_ID = uuid.UUID("44444444-4444-4444-4444-444444444444")
_PLAN_HASH = "abc123def456" + "f" * 52  # 64 hex chars


@dataclass
class _FakeRetrievalService:
    """Stand-in for :class:`ArticleRagRetrievalService`.

    The fake records every call (``calls``) and returns whatever
    ``result_factory`` produces.  Tests configure either
    ``result_factory`` (happy path) or ``raise_exc`` (error path) —
    never both.  ``raise_exc`` takes precedence.
    """

    calls: list[dict[str, Any]] = field(default_factory=list)
    result_factory: "callable | None" = None
    raise_exc: Exception | None = None

    async def retrieve_for_record(
        self,
        *,
        reading_record_id: uuid.UUID,
        user_id: uuid.UUID,
        query_text: str,
        limit: int = DEFAULT_LIMIT,
        index_version: str = DEFAULT_INDEX_VERSION,
    ) -> ArticleRagRetrievalResult:
        self.calls.append(
            {
                "reading_record_id": str(reading_record_id),
                "user_id": str(user_id),
                "query_text": query_text,
                "limit": int(limit),
                "index_version": index_version,
            }
        )
        if self.raise_exc is not None:
            raise self.raise_exc
        assert self.result_factory is not None
        return self.result_factory(
            reading_record_id=reading_record_id,
            limit=limit,
            index_version=index_version,
        )


def _make_retrieval_result(
    *,
    hits: list[ArticleRagRetrievalHit] | None = None,
) -> ArticleRagRetrievalResult:
    return ArticleRagRetrievalResult(
        reading_record_id=_RECORD_ID,
        stable_document_id=_STABLE_DOC_ID,
        base_id=_BASE_ID,
        record_generation=1,
        index_version=DEFAULT_INDEX_VERSION,
        plan_content_sha256=_PLAN_HASH,
        hits=tuple(hits or ()),
        provider_metadata={"provider": "fake-in-memory"},
    )


def _make_hit(
    *,
    chunk_id: str,
    text: str,
    score: float = 0.9,
    citation: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> ArticleRagRetrievalHit:
    return ArticleRagRetrievalHit(
        chunk_id=chunk_id,
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
        score=score,
    )


def _build_service(
    *,
    retrieval: _FakeRetrievalService | None = None,
) -> ArticleRagContextService:
    return ArticleRagContextService(
        retrieval_service=retrieval
        or _FakeRetrievalService(
            result_factory=lambda **kw: _make_retrieval_result()
        ),
    )


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_happy_path_fields_complete_and_order_stable() -> None:
    hits = [
        _make_hit(chunk_id="c1", text="alpha", score=0.95),
        _make_hit(chunk_id="c2", text="beta", score=0.85),
        _make_hit(chunk_id="c3", text="gamma", score=0.75),
    ]
    retrieval = _FakeRetrievalService(
        result_factory=lambda **kw: _make_retrieval_result(hits=hits)
    )
    service = _build_service(retrieval=retrieval)
    pack = await service.build_context_pack_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello world",
        limit=5,
    )
    assert isinstance(pack, ArticleRagContextPack)
    # Order preserved (score descending).
    assert [item.chunk_id for item in pack.items] == ["c1", "c2", "c3"]
    assert [item.rank for item in pack.items] == [1, 2, 3]
    assert [item.context_id for item in pack.items] == [
        "rag-1",
        "rag-2",
        "rag-3",
    ]
    assert [item.text for item in pack.items] == ["alpha", "beta", "gamma"]
    assert [item.score for item in pack.items] == [0.95, 0.85, 0.75]
    # Citation comes verbatim from the retrieval hit.
    assert pack.items[0].citation["block_ids"] == ["block-for-c1"]
    # Retrieval-result fields are echoed on the pack.
    assert pack.reading_record_id == _RECORD_ID
    assert pack.stable_document_id == _STABLE_DOC_ID
    assert pack.base_id == _BASE_ID
    assert pack.index_version == DEFAULT_INDEX_VERSION
    assert pack.plan_content_sha256 == _PLAN_HASH
    # provider_metadata is passed through (ops diagnostic only).
    assert pack.provider_metadata == {"provider": "fake-in-memory"}


@pytest.mark.anyio
async def test_happy_path_retrieval_called_with_correct_kwargs() -> None:
    hits = [_make_hit(chunk_id="c1", text="a")]
    retrieval = _FakeRetrievalService(
        result_factory=lambda **kw: _make_retrieval_result(hits=hits)
    )
    service = _build_service(retrieval=retrieval)
    await service.build_context_pack_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
        limit=7,
        index_version="custom-v2",
    )
    assert len(retrieval.calls) == 1
    call = retrieval.calls[0]
    assert call["reading_record_id"] == str(_RECORD_ID)
    assert call["user_id"] == str(_USER_ID)
    assert call["query_text"] == "hello"
    assert call["limit"] == 7
    assert call["index_version"] == "custom-v2"


# ---------------------------------------------------------------------------
# 2. Zero hits is not an error
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_zero_hits_returns_empty_pack() -> None:
    retrieval = _FakeRetrievalService(
        result_factory=lambda **kw: _make_retrieval_result(hits=[])
    )
    service = _build_service(retrieval=retrieval)
    pack = await service.build_context_pack_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
    )
    assert pack.items == ()
    assert pack.total_text_chars == 0
    assert pack.omitted_hit_count == 0
    assert pack.budget_exceeded is False
    # Authoritative fields still echoed.
    assert pack.stable_document_id == _STABLE_DOC_ID
    assert pack.plan_content_sha256 == _PLAN_HASH


# ---------------------------------------------------------------------------
# 3. query_text never appears on the result or in any error
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_query_text_not_on_result_only_query_sha256() -> None:
    secret = "SECRET-QUERY-DO-NOT-LEAK-DO-NOT-LEAK"
    hits = [_make_hit(chunk_id="c1", text="alpha")]
    retrieval = _FakeRetrievalService(
        result_factory=lambda **kw: _make_retrieval_result(hits=hits)
    )
    service = _build_service(retrieval=retrieval)
    pack = await service.build_context_pack_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text=secret,
    )
    assert secret not in str(pack.query_sha256)
    assert pack.query_sha256 == hashlib.sha256(secret.encode("utf-8")).hexdigest()
    # Defence in depth: the secret must not appear anywhere in the
    # serialised pack (e.g. via repr).
    assert secret not in repr(pack)


@pytest.mark.anyio
async def test_query_text_not_in_blank_query_error_message() -> None:
    service = _build_service()
    with pytest.raises(ArticleRagContextServiceError) as exc_info:
        await service.build_context_pack_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="",
        )
    msg = str(exc_info.value)
    # The empty string has nothing to leak — but assert the error
    # does not echo any caller-supplied identifier either.
    assert _RECORD_ID.__str__() not in msg
    assert _USER_ID.__str__() not in msg


@pytest.mark.anyio
async def test_query_text_not_in_retrieval_error_message() -> None:
    secret = "SECRET-QUERY-DO-NOT-LEAK-DO-NOT-LEAK"
    retrieval = _FakeRetrievalService(
        raise_exc=ArticleRagRetrievalServiceError(
            "retrieval service exploded for record "
            f"{_RECORD_ID}",
            retryable=False,
            failure_code="retrieval_exploded",
        )
    )
    service = _build_service(retrieval=retrieval)
    with pytest.raises(ArticleRagContextServiceError) as exc_info:
        await service.build_context_pack_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text=secret,
        )
    msg = str(exc_info.value)
    # Defence in depth: secret query must not appear anywhere in
    # the rewritten diagnostic.
    assert secret not in msg


# ---------------------------------------------------------------------------
# 4. Character budget — normal truncation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_budget_truncates_after_first_oversized_fits() -> None:
    hits = [
        _make_hit(chunk_id="c1", text="a" * 100, score=0.9),
        _make_hit(chunk_id="c2", text="b" * 50, score=0.8),
        _make_hit(chunk_id="c3", text="c" * 10, score=0.7),
    ]
    retrieval = _FakeRetrievalService(
        result_factory=lambda **kw: _make_retrieval_result(hits=hits)
    )
    service = _build_service(retrieval=retrieval)
    pack = await service.build_context_pack_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
        limit=5,
        max_context_chars=120,
    )
    # Contract: once a hit does not fit, the loop BREAKS.  We do NOT
    # skip the unfit hit and try to add the next one.  Here c1=100
    # fits (100 <= 120); c2=50 does not (100+50=150 > 120); c3 is
    # therefore NOT included even though it would have fit on its
    # own (10 chars).  Only c1 makes it.
    assert [item.chunk_id for item in pack.items] == ["c1"]
    assert pack.total_text_chars == 100
    # omitted counts BOTH the unfit hit (c2) and everything after it
    # (c3), because the loop broke.
    assert pack.omitted_hit_count == 2
    assert pack.budget_exceeded is False


@pytest.mark.anyio
async def test_middle_oversized_but_later_small_hit_must_not_be_included() -> (
    None
):
    """P1 reviewer fix: ``c1=80, c2=50, c3=10`` with ``budget=100``
    must yield ``items=[c1]`` (omitted=2), NOT ``items=[c1, c3]``.

    A naive ``continue`` would silently skip c2 and include c3,
    breaking the score-descending contiguous-prefix invariant and
    producing rank/context_id numbers that no longer correspond to
    the retrieval order.  The pack MUST be a prefix of the
    score-sorted hits list.
    """
    hits = [
        _make_hit(chunk_id="c1", text="a" * 80, score=0.9),
        _make_hit(chunk_id="c2", text="b" * 50, score=0.8),
        _make_hit(chunk_id="c3", text="c" * 10, score=0.7),
    ]
    retrieval = _FakeRetrievalService(
        result_factory=lambda **kw: _make_retrieval_result(hits=hits)
    )
    service = _build_service(retrieval=retrieval)
    pack = await service.build_context_pack_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
        max_context_chars=100,
    )
    # c1 fits (80).  c2 does not (80+50=130 > 100).  Loop BREAKS —
    # c3 is NOT included even though it would have fit on its own.
    assert [item.chunk_id for item in pack.items] == ["c1"]
    assert [item.rank for item in pack.items] == [1]
    assert [item.context_id for item in pack.items] == ["rag-1"]
    # omitted = (len(hits) - (rank - 1)) = 3 - 1 = 2 (c2 + c3).
    assert pack.omitted_hit_count == 2
    assert pack.total_text_chars == 80
    assert pack.budget_exceeded is False


@pytest.mark.anyio
async def test_budget_truncation_correctly_counts_omitted_hits() -> None:
    """Hits that don't fit are counted in ``omitted_hit_count``;
    they do NOT silently disappear from the trace."""
    hits = [
        _make_hit(chunk_id="c1", text="a" * 60, score=0.9),
        _make_hit(chunk_id="c2", text="b" * 60, score=0.8),
        _make_hit(chunk_id="c3", text="c" * 60, score=0.7),
    ]
    retrieval = _FakeRetrievalService(
        result_factory=lambda **kw: _make_retrieval_result(hits=hits)
    )
    service = _build_service(retrieval=retrieval)
    pack = await service.build_context_pack_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
        max_context_chars=100,
    )
    # c1 = 60, c2 = 60 → 120 > 100 → c2 omitted.  c3 = 60 → 60+60=120
    # > 100 → c3 omitted.
    assert [item.chunk_id for item in pack.items] == ["c1"]
    assert pack.omitted_hit_count == 2
    assert pack.total_text_chars == 60


@pytest.mark.anyio
async def test_budget_first_chunk_oversized_kept_with_budget_exceeded() -> None:
    """When the first chunk is itself larger than the budget, it is
    still kept intact and ``budget_exceeded=True`` is set.  The
    LLM consumer can decide whether to truncate further."""
    hits = [
        _make_hit(chunk_id="c1", text="x" * 500, score=0.9),
        _make_hit(chunk_id="c2", text="y" * 50, score=0.8),
    ]
    retrieval = _FakeRetrievalService(
        result_factory=lambda **kw: _make_retrieval_result(hits=hits)
    )
    service = _build_service(retrieval=retrieval)
    pack = await service.build_context_pack_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
        max_context_chars=100,
    )
    assert [item.chunk_id for item in pack.items] == ["c1"]
    assert pack.total_text_chars == 500
    assert pack.budget_exceeded is True
    # c2 did not fit, so it is counted as omitted.
    assert pack.omitted_hit_count == 1


@pytest.mark.anyio
async def test_budget_exceeded_false_when_first_chunk_fits() -> None:
    hits = [
        _make_hit(chunk_id="c1", text="a" * 50, score=0.9),
        _make_hit(chunk_id="c2", text="b" * 40, score=0.8),
    ]
    retrieval = _FakeRetrievalService(
        result_factory=lambda **kw: _make_retrieval_result(hits=hits)
    )
    service = _build_service(retrieval=retrieval)
    pack = await service.build_context_pack_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
        max_context_chars=100,
    )
    assert pack.budget_exceeded is False
    assert [item.chunk_id for item in pack.items] == ["c1", "c2"]


@pytest.mark.anyio
async def test_default_budget_is_4000_chars() -> None:
    assert DEFAULT_MAX_CONTEXT_CHARS == 4000


# ---------------------------------------------------------------------------
# 5. Metadata denylist scrub (defence in depth)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_metadata_denylist_strips_forbidden_keys() -> None:
    """A future regression in the retrieval path must not leak Plate /
    Markdown / DOM / Slate / UI display group / text / html keys.
    The context service re-scrubs."""
    hits = [
        _make_hit(
            chunk_id="c1",
            text="a",
            metadata={
                "block_type": "paragraph",
                # Forbidden keys that MUST be scrubbed:
                "plate": {"op": "slate"},
                "plate_json": {"node": "x"},
                "markdown": "**hello**",
                "markdown_syntax": "# title",
                "dom": {"tag": "div"},
                "dom_selection": "xpath",
                "slate": {"path": [0, 1]},
                "slate_path": [0, 1],
                "ui": {"display": "x"},
                "ui_display_group": "main",
                "render_profile": "v1",
                "render_snapshot": {"a": 1},
                "text": "SECRET-CHUNK-TEXT-DO-NOT-LEAK",
                "html": "<p>hi</p>",
                "innerText": "hi",
                "innerHTML": "<b>x</b>",
                "citation_refs": [{"ref": "x"}],
                "chunks": ["x", "y"],
                "chunk_text": "secret",
                "chunk_texts": ["secret"],
            },
        ),
    ]
    retrieval = _FakeRetrievalService(
        result_factory=lambda **kw: _make_retrieval_result(hits=hits)
    )
    service = _build_service(retrieval=retrieval)
    pack = await service.build_context_pack_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
    )
    md = pack.items[0].metadata_json
    forbidden = {
        "plate",
        "plate_json",
        "markdown",
        "markdown_syntax",
        "dom",
        "dom_selection",
        "slate",
        "slate_path",
        "ui",
        "ui_display_group",
        "render_profile",
        "render_snapshot",
        "text",
        "html",
        "innerText",
        "innerHTML",
        "citation_refs",
        "chunks",
        "chunk_text",
        "chunk_texts",
    }
    leaked = forbidden & set(md.keys())
    assert leaked == set(), f"forbidden keys leaked: {leaked}"
    # Safe metadata preserved.
    assert md["block_type"] == "paragraph"
    # The chunk text MUST NOT appear in metadata_json.
    assert "SECRET-CHUNK-TEXT-DO-NOT-LEAK" not in str(md)


@pytest.mark.anyio
async def test_metadata_tags_chunk_id_and_context_id() -> None:
    hits = [_make_hit(chunk_id="c1", text="a", metadata={"x": 1})]
    retrieval = _FakeRetrievalService(
        result_factory=lambda **kw: _make_retrieval_result(hits=hits)
    )
    service = _build_service(retrieval=retrieval)
    pack = await service.build_context_pack_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
    )
    md = pack.items[0].metadata_json
    assert md["chunk_id"] == "c1"
    assert md["context_id"] == "rag-1"


# ---------------------------------------------------------------------------
# 6. Citation comes verbatim from the retrieval hit
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_citation_preserved_verbatim_from_retrieval_hit() -> None:
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
    hits = [
        _make_hit(
            chunk_id="c1", text="a", citation=custom_citation
        )
    ]
    retrieval = _FakeRetrievalService(
        result_factory=lambda **kw: _make_retrieval_result(hits=hits)
    )
    service = _build_service(retrieval=retrieval)
    pack = await service.build_context_pack_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
    )
    # Citation is preserved verbatim — the context service does NOT
    # rebuild or rewrite it.
    assert pack.items[0].citation == custom_citation
    # Defence in depth: no forbidden projection fields appear in the
    # citation (they were never there to begin with — sanity check).
    for forbidden in (
        "plate",
        "markdown",
        "dom",
        "slate",
        "ui",
        "render_profile",
    ):
        assert forbidden not in pack.items[0].citation


# ---------------------------------------------------------------------------
# 7. Retrieval error → typed ArticleRagContextServiceError with __cause__
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_retrieval_typed_error_wrapped_with_cause() -> None:
    underlying = ArticleRagRetrievalServiceError(
        "retrieval exploded",
        retryable=False,
        failure_code="retrieval_exploded",
    )
    retrieval = _FakeRetrievalService(raise_exc=underlying)
    service = _build_service(retrieval=retrieval)
    with pytest.raises(ArticleRagContextServiceError) as exc_info:
        await service.build_context_pack_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="hello",
        )
    err = exc_info.value
    assert err.failure_code == FAILURE_CODE_CONTEXT_RETRIEVAL_FAILED
    # Original exception preserved as __cause__.
    assert err.__cause__ is underlying
    # Retryable propagated.
    assert err.retryable is False
    # Original message NOT echoed.
    assert "retrieval exploded" not in str(err)


@pytest.mark.anyio
async def test_retrieval_uncaught_exception_wrapped() -> None:
    class _BareRaisingRetrieval(_FakeRetrievalService):
        async def retrieve_for_record(self, **kwargs: Any) -> Any:
            self.calls.append(kwargs)
            raise RuntimeError("totally unexpected SDK boom")

    retrieval = _BareRaisingRetrieval()
    service = ArticleRagContextService(retrieval_service=retrieval)
    with pytest.raises(ArticleRagContextServiceError) as exc_info:
        await service.build_context_pack_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="hello",
        )
    assert exc_info.value.failure_code == FAILURE_CODE_CONTEXT_RETRIEVAL_FAILED
    assert isinstance(exc_info.value.__cause__, RuntimeError)


@pytest.mark.anyio
async def test_no_retrieval_service_configured_fails_closed() -> None:
    service = ArticleRagContextService()  # no retrieval_service
    with pytest.raises(ArticleRagContextServiceError) as exc_info:
        await service.build_context_pack_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="hello",
        )
    assert exc_info.value.failure_code == FAILURE_CODE_CONTEXT_RETRIEVAL_FAILED


@pytest.mark.anyio
async def test_context_error_inherits_worker_error() -> None:
    """Defence in depth: the context error must inherit the worker
    base class so any future orchestrator that catches the worker
    base class also catches context failures."""
    err = ArticleRagContextServiceError(
        "synthetic",
        retryable=False,
        failure_code=FAILURE_CODE_CONTEXT_EMPTY_QUERY,
    )
    assert isinstance(err, ArticleRagIndexWorkerError)


# ---------------------------------------------------------------------------
# 8. Invalid limit / max_context_chars / blank query_text fail closed
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_empty_query_fails_closed() -> None:
    service = _build_service()
    with pytest.raises(ArticleRagContextServiceError) as exc_info:
        await service.build_context_pack_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="",
        )
    assert exc_info.value.failure_code == FAILURE_CODE_CONTEXT_EMPTY_QUERY
    # Retrieval service MUST NOT have been called.
    retrieval = service._retrieval_service  # type: ignore[attr-defined]
    assert retrieval.calls == []


@pytest.mark.anyio
async def test_whitespace_only_query_fails_closed() -> None:
    service = _build_service()
    with pytest.raises(ArticleRagContextServiceError) as exc_info:
        await service.build_context_pack_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="   \t\n  ",
        )
    assert exc_info.value.failure_code == FAILURE_CODE_CONTEXT_EMPTY_QUERY


@pytest.mark.anyio
async def test_zero_limit_fails_closed() -> None:
    service = _build_service()
    with pytest.raises(ArticleRagContextServiceError) as exc_info:
        await service.build_context_pack_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="hello",
            limit=0,
        )
    assert exc_info.value.failure_code == FAILURE_CODE_CONTEXT_INVALID_LIMIT


@pytest.mark.anyio
async def test_negative_limit_fails_closed() -> None:
    service = _build_service()
    with pytest.raises(ArticleRagContextServiceError) as exc_info:
        await service.build_context_pack_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="hello",
            limit=-3,
        )
    assert exc_info.value.failure_code == FAILURE_CODE_CONTEXT_INVALID_LIMIT


@pytest.mark.anyio
async def test_oversized_limit_fails_closed() -> None:
    service = _build_service()
    with pytest.raises(ArticleRagContextServiceError) as exc_info:
        await service.build_context_pack_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="hello",
            limit=MAX_RETRIEVAL_LIMIT + 1,
        )
    assert exc_info.value.failure_code == FAILURE_CODE_CONTEXT_INVALID_LIMIT


@pytest.mark.anyio
async def test_zero_budget_fails_closed() -> None:
    service = _build_service()
    with pytest.raises(ArticleRagContextServiceError) as exc_info:
        await service.build_context_pack_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="hello",
            max_context_chars=0,
        )
    assert exc_info.value.failure_code == FAILURE_CODE_CONTEXT_INVALID_BUDGET


@pytest.mark.anyio
async def test_negative_budget_fails_closed() -> None:
    service = _build_service()
    with pytest.raises(ArticleRagContextServiceError) as exc_info:
        await service.build_context_pack_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="hello",
            max_context_chars=-10,
        )
    assert exc_info.value.failure_code == FAILURE_CODE_CONTEXT_INVALID_BUDGET


# ---------------------------------------------------------------------------
# 9. Constants / exports
# ---------------------------------------------------------------------------


def test_default_limit_constant() -> None:
    assert DEFAULT_LIMIT == 8


def test_failure_codes_are_distinct() -> None:
    codes = {
        FAILURE_CODE_CONTEXT_EMPTY_QUERY,
        FAILURE_CODE_CONTEXT_INVALID_LIMIT,
        FAILURE_CODE_CONTEXT_INVALID_BUDGET,
        FAILURE_CODE_CONTEXT_RETRIEVAL_FAILED,
    }
    assert len(codes) == 4


# ---------------------------------------------------------------------------
# 10. Determinism
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_pack_deterministic_for_same_input() -> None:
    hits = [
        _make_hit(chunk_id="c1", text="a" * 60, score=0.9),
        _make_hit(chunk_id="c2", text="b" * 60, score=0.8),
        _make_hit(chunk_id="c3", text="c" * 60, score=0.7),
    ]

    async def _run_once() -> ArticleRagContextPack:
        retrieval = _FakeRetrievalService(
            result_factory=lambda **kw: _make_retrieval_result(hits=hits)
        )
        service = _build_service(retrieval=retrieval)
        return await service.build_context_pack_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="hello",
            max_context_chars=100,
        )

    pack_a = await _run_once()
    pack_b = await _run_once()
    # Same query text → same query_sha256.
    assert pack_a.query_sha256 == pack_b.query_sha256
    # Same item set + ranks + scores + text.
    assert (
        pack_a.items == pack_b.items
    )
    # Same budget + omission accounting.
    assert pack_a.total_text_chars == pack_b.total_text_chars
    assert pack_a.omitted_hit_count == pack_b.omitted_hit_count
    assert pack_a.budget_exceeded == pack_b.budget_exceeded


# ---------------------------------------------------------------------------
# 11. Reviewer P2 fix: provider_metadata must be scrubbed before forwarding
# ---------------------------------------------------------------------------


def _make_retrieval_result_with_provider_metadata(
    *,
    provider_metadata: dict[str, Any],
    hits: list[ArticleRagRetrievalHit] | None = None,
) -> ArticleRagRetrievalResult:
    base = _make_retrieval_result(hits=hits)
    return ArticleRagRetrievalResult(
        reading_record_id=base.reading_record_id,
        stable_document_id=base.stable_document_id,
        base_id=base.base_id,
        record_generation=base.record_generation,
        index_version=base.index_version,
        plan_content_sha256=base.plan_content_sha256,
        hits=base.hits,
        provider_metadata=provider_metadata,
    )


@pytest.mark.anyio
async def test_provider_metadata_drops_query_token_uri() -> None:
    """A future regression in the retrieval path could surface
    credentials or query content on ``provider_metadata``.  The
    context service must scrub these keys before forwarding."""
    secret_query = "SECRET-QUERY-DO-NOT-LEAK-DO-NOT-LEAK"
    secret_token = "SECRET-ZILLIZ-TOKEN-DO-NOT-LEAK"
    secret_uri = "https://secret.zilliz.example.com"
    secret_key = "SECRET-BAILIAN-API-KEY"
    provider_metadata = {
        "provider": "zilliz",
        "query_text": secret_query,
        "query": secret_query,
        "query_vector": [0.1, 0.2, 0.3],
        "token": secret_token,
        "zilliz_token": secret_token,
        "uri": secret_uri,
        "zilliz_uri": secret_uri,
        "url": secret_uri,
        "secret": "SECRET-RAW",
        "key": secret_key,
        "api_key": secret_key,
        "bailian_api_key": secret_key,
        "sdk_message": "SECRET-SDK-DIAGNOSTIC-DO-NOT-LEAK",
        "error_message": "SECRET-ERR-DO-NOT-LEAK",
        "raw_message": "SECRET-RAW-MSG-DO-NOT-LEAK",
    }
    retrieval = _FakeRetrievalService(
        result_factory=lambda **kw: _make_retrieval_result_with_provider_metadata(
            provider_metadata=provider_metadata
        )
    )
    service = _build_service(retrieval=retrieval)
    pack = await service.build_context_pack_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
    )
    # The safe ``provider`` key survives; everything forbidden is gone.
    assert pack.provider_metadata == {"provider": "zilliz"}


@pytest.mark.anyio
async def test_provider_metadata_drops_projection_keys() -> None:
    """Defence in depth: even if a regression in the retrieval path
    puts Plate / Markdown / DOM / Slate / UI display group / render /
    text / html keys on ``provider_metadata``, they MUST NOT appear
    on the pack."""
    provider_metadata = {
        "provider": "zilliz",
        "plate": {"op": "slate"},
        "markdown": "**hello**",
        "dom": {"tag": "div"},
        "slate": {"path": [0, 1]},
        "ui": {"display": "x"},
        "render_profile": "v1",
        "text": "SECRET-CHUNK-TEXT",
        "html": "<p>x</p>",
        "citation_refs": [{"ref": "x"}],
    }
    retrieval = _FakeRetrievalService(
        result_factory=lambda **kw: _make_retrieval_result_with_provider_metadata(
            provider_metadata=provider_metadata
        )
    )
    service = _build_service(retrieval=retrieval)
    pack = await service.build_context_pack_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
    )
    assert pack.provider_metadata == {"provider": "zilliz"}


@pytest.mark.anyio
async def test_provider_metadata_scrub_applied_on_empty_hits_branch() -> None:
    """The scrub must run on BOTH branches: empty hits and full
    hits.  The empty-hits branch was previously a separate call site
    that the reviewer flagged for the same risk."""
    secret_token = "SECRET-ZILLIZ-TOKEN"
    provider_metadata = {
        "provider": "zilliz",
        "token": secret_token,
        "query_text": "SECRET-QUERY",
    }
    retrieval = _FakeRetrievalService(
        result_factory=lambda **kw: _make_retrieval_result_with_provider_metadata(
            provider_metadata=provider_metadata, hits=[]
        )
    )
    service = _build_service(retrieval=retrieval)
    pack = await service.build_context_pack_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
    )
    assert pack.items == ()
    assert pack.provider_metadata == {"provider": "zilliz"}


@pytest.mark.anyio
async def test_provider_metadata_repr_does_not_leak_secret_query() -> None:
    """The reviewer explicitly asked: ``repr(pack)`` must not leak
    even when ``provider_metadata`` contains the secret query.  This
    is the key user-visible surface in Python debugging."""
    secret_query = "SECRET-QUERY-DO-NOT-LEAK-DO-NOT-LEAK"
    provider_metadata = {
        "provider": "zilliz",
        "query_text": secret_query,
        "query": secret_query,
    }
    retrieval = _FakeRetrievalService(
        result_factory=lambda **kw: _make_retrieval_result_with_provider_metadata(
            provider_metadata=provider_metadata
        )
    )
    service = _build_service(retrieval=retrieval)
    pack = await service.build_context_pack_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text=secret_query,
    )
    repr_text = repr(pack)
    assert secret_query not in repr_text
    # And the same for ``str(pack)``.
    assert secret_query not in str(pack)


@pytest.mark.anyio
async def test_provider_metadata_nested_secrets_scrubbed() -> None:
    """Defence in depth: the new whitelist is scalar-only.  Even a
    whitelisted key (``provider``) whose value is a NESTED dict
    (e.g. ``{"name": "zilliz", "token": "..."}``) MUST be dropped
    entirely — a dict value is not a safe scalar, and the nested
    structure could leak any number of unclassified fields.

    A future regression like
    ``{"provider": {"name": "zilliz", "token": "SECRET"}}``
    is caught by the ``_safe_scalar_value`` predicate (which
    rejects dict values) regardless of whether the nested key
    names are individually safe.
    """
    secret_token = "SECRET-NESTED-TOKEN"
    provider_metadata = {
        "provider": {
            "name": "zilliz",
            "token": secret_token,
            "details": {
                "uri": "https://secret.zilliz.example.com",
                "extra": {"api_key": "SECRET-NESTED-KEY"},
            },
        },
    }
    retrieval = _FakeRetrievalService(
        result_factory=lambda **kw: _make_retrieval_result_with_provider_metadata(
            provider_metadata=provider_metadata
        )
    )
    service = _build_service(retrieval=retrieval)
    pack = await service.build_context_pack_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
    )
    # Strict-whitelist contract: the value is a dict, not a scalar
    # → the entire ``provider`` entry is dropped.  The result is
    # empty, NOT a partly-scrubbed nested dict.
    assert pack.provider_metadata == {}
    # Defence in depth: no secret substring leaks anywhere in the
    # serialised pack.
    repr_text = repr(pack)
    for secret in (
        secret_token,
        "https://secret.zilliz.example.com",
        "SECRET-NESTED-KEY",
    ):
        assert secret not in repr_text


@pytest.mark.anyio
async def test_provider_metadata_handles_none_and_empty() -> None:
    """Boundary: ``None`` and ``{}`` on ``provider_metadata`` must
    produce an empty dict on the pack, not raise."""
    for empty in (None, {}):
        retrieval = _FakeRetrievalService(
            result_factory=lambda **kw: _make_retrieval_result_with_provider_metadata(
                provider_metadata=empty, hits=[]
            )
        )
        service = _build_service(retrieval=retrieval)
        pack = await service.build_context_pack_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="hello",
        )
        assert pack.provider_metadata == {}


# ---------------------------------------------------------------------------
# 12. Reviewer P2 fix (round 2): whitelist, not denylist
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_provider_metadata_drops_unknown_scalar_secret_key() -> None:
    """The reviewer explicitly asked: a UNKNOWN key whose value is
    a secret must NOT appear in ``provider_metadata`` — and therefore
    must NOT leak in ``repr(pack)``.

    With the old denylist, ``{"diagnostic": "SECRET-QUERY..."}``
    passed through because ``diagnostic`` is not in the denylist.
    With the new whitelist, ``diagnostic`` is not whitelisted → the
    entire entry is dropped.
    """
    secret = "SECRET-QUERY-DO-NOT-LEAK-DO-NOT-LEAK"
    provider_metadata = {
        "diagnostic": secret,
        "internal_state": {"foo": "bar"},
    }
    retrieval = _FakeRetrievalService(
        result_factory=lambda **kw: _make_retrieval_result_with_provider_metadata(
            provider_metadata=provider_metadata
        )
    )
    service = _build_service(retrieval=retrieval)
    pack = await service.build_context_pack_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
    )
    assert pack.provider_metadata == {}
    assert secret not in repr(pack)
    assert secret not in str(pack)


@pytest.mark.anyio
async def test_provider_metadata_drops_list_value_containing_secret() -> None:
    """The reviewer explicitly asked: ``{"provider": ["token=..."]}``
    must NOT appear in ``repr(pack)``.

    ``provider`` is whitelisted, but the value is a list, not a
    scalar.  The ``_safe_scalar_value`` predicate rejects lists
    entirely, so the entire ``provider`` entry is dropped.
    """
    secret = "SECRET-TOKEN-DO-NOT-LEAK"
    provider_metadata = {
        "provider": [f"token={secret}"],
        "collection": ["another-secret"],
    }
    retrieval = _FakeRetrievalService(
        result_factory=lambda **kw: _make_retrieval_result_with_provider_metadata(
            provider_metadata=provider_metadata
        )
    )
    service = _build_service(retrieval=retrieval)
    pack = await service.build_context_pack_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
    )
    assert pack.provider_metadata == {}
    assert secret not in repr(pack)
    assert "another-secret" not in repr(pack)


@pytest.mark.anyio
async def test_provider_metadata_drops_whitelisted_key_with_secret_value() -> (
    None
):
    """The reviewer explicitly asked: a SAFE whitelisted key with a
    NESTED dict whose inner value is a secret must NOT leak.

    Even though ``provider`` is whitelisted, the value
    ``{"name": "zilliz", "secret": "..."}`` is a dict (not a
    scalar).  The ``_safe_scalar_value`` predicate rejects it, so
    the entire entry is dropped — no partial scrub, no nested walk.
    """
    secret = "SECRET-INNER-DO-NOT-LEAK"
    provider_metadata = {
        "provider": {"name": "zilliz", "secret": secret},
        "collection": {"name": "c1", "uri": "https://secret"},
    }
    retrieval = _FakeRetrievalService(
        result_factory=lambda **kw: _make_retrieval_result_with_provider_metadata(
            provider_metadata=provider_metadata
        )
    )
    service = _build_service(retrieval=retrieval)
    pack = await service.build_context_pack_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
    )
    assert pack.provider_metadata == {}
    assert secret not in repr(pack)


@pytest.mark.anyio
async def test_provider_metadata_rejects_whitelisted_key_with_substring() -> (
    None
):
    """Even when a whitelisted key has a SCALAR value, the value-side
    predicate still rejects anything containing a forbidden
    substring (case-insensitive).  E.g.
    ``{"provider": "zilliz-token=ABC"}`` is a regression that the
    key-only whitelist cannot catch — but the value predicate does.
    """
    retrieval = _FakeRetrievalService(
        result_factory=lambda **kw: _make_retrieval_result_with_provider_metadata(
            provider_metadata={
                "provider": "zilliz-token=ABC",
                "collection": "https://secret.zilliz.example.com",
                "embedding_model": "text-embedding-v4 (api_key=XYZ)",
            }
        )
    )
    service = _build_service(retrieval=retrieval)
    pack = await service.build_context_pack_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
    )
    assert pack.provider_metadata == {}
    assert "ABC" not in repr(pack)
    assert "secret.zilliz" not in repr(pack)
    assert "XYZ" not in repr(pack)


@pytest.mark.anyio
async def test_provider_metadata_rejects_overly_long_value() -> None:
    """A whitelisted key whose scalar value exceeds the length cap
    is dropped — long values are almost certainly regressions
    (a "provider" name should not be 1000 chars)."""
    long_value = "x" * 1000
    retrieval = _FakeRetrievalService(
        result_factory=lambda **kw: _make_retrieval_result_with_provider_metadata(
            provider_metadata={
                "provider": long_value,
                "collection": "c1",
            }
        )
    )
    service = _build_service(retrieval=retrieval)
    pack = await service.build_context_pack_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
    )
    # ``provider`` is dropped (long value), ``collection`` survives.
    assert pack.provider_metadata == {"collection": "c1"}


@pytest.mark.anyio
async def test_provider_metadata_keeps_only_whitelisted_scalar_keys() -> None:
    """Positive control: a payload with the full whitelisted key
    set (all scalars) round-trips through the scrub unchanged."""
    provider_metadata = {
        "provider": "zilliz",
        "collection": "article_rag_index_v1",
        "hit_count": 5,
        "limit": 8,
        "latency_ms": 42,
        "total_latency_ms": 100,
        "embedding_model": "text-embedding-v4",
        "index_version": "article_rag_index_v1",
        "plan_content_sha256": "abc123" + "f" * 58,
        "region": "us-west-2",
        "namespace": "default",
    }
    retrieval = _FakeRetrievalService(
        result_factory=lambda **kw: _make_retrieval_result_with_provider_metadata(
            provider_metadata=provider_metadata
        )
    )
    service = _build_service(retrieval=retrieval)
    pack = await service.build_context_pack_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
    )
    # All keys present, all values preserved.
    assert pack.provider_metadata == {
        k: v for k, v in provider_metadata.items()
    }