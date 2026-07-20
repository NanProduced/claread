"""D6-I4I: tests for Article RAG ask prompt attachment service.

Covers:
  * available path includes context + citations + context_ids +
    source_pack_hash.
  * disabled / empty / not_indexed_or_unavailable / composer_rejected
    paths all produce a no-context attachment with
    ``fallback_allowed=True``.
  * unexpected resolver exception is wrapped fail-soft and does
    NOT leak query_text or upstream message.
  * ``query_text`` never appears in ``repr(attachment)`` /
    ``str(attachment)``.
  * ``provider_metadata`` never appears on the attachment.
  * ``prompt_context_text`` is EXACTLY the I4G composer output —
    no mutation, no annotation with citation JSON, no projection
    keys.
  * citations stay structured (one dict per item, in score-
    descending order).
  * stable ids and budget fields are preserved when available.
  * malformed resolver result (status="available" + bundle=None)
    fails soft (does not crash).
  * missing resolver config fails soft.
  * no DB / network / LLM.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.services.reader_orchestration.article_rag_ask_context_composer import (
    ArticleRagAskContextBundle,
    ArticleRagAskContextCitation,
)
from app.services.reader_orchestration.article_rag_ask_context_resolver import (
    ArticleRagAskContextResolveResult,
)
from app.services.reader_orchestration.article_rag_ask_prompt_attachment import (
    DEFAULT_ATTACHMENT_LIMIT,
    DEFAULT_ATTACHMENT_MAX_CONTEXT_CHARS,
    FAILURE_CODE_ATTACHMENT_UNEXPECTED_ERROR,
    ArticleRagAskPromptAttachment,
    ArticleRagAskPromptAttachmentService,
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


@dataclass
class _FakeResolver:
    """Stand-in for :class:`ArticleRagAskContextResolver`.

    Configure either ``result_factory`` (happy path) or
    ``raise_exc`` (error path) — never both.  ``raise_exc`` takes
    precedence.  Records every call.
    """

    calls: list[dict[str, Any]] = field(default_factory=list)
    result_factory: "callable | None" = None
    raise_exc: Exception | None = None

    async def resolve_for_record(
        self,
        *,
        reading_record_id: uuid.UUID,
        user_id: uuid.UUID,
        query_text: str,
        enabled: bool = True,
        limit: int = DEFAULT_ATTACHMENT_LIMIT,
        max_context_chars: int = DEFAULT_ATTACHMENT_MAX_CONTEXT_CHARS,
    ) -> ArticleRagAskContextResolveResult:
        self.calls.append(
            {
                "reading_record_id": str(reading_record_id),
                "user_id": str(user_id),
                "query_text": query_text,
                "enabled": bool(enabled),
                "limit": int(limit),
                "max_context_chars": int(max_context_chars),
            }
        )
        if self.raise_exc is not None:
            raise self.raise_exc
        assert self.result_factory is not None
        return self.result_factory(
            reading_record_id=reading_record_id, query_text=query_text
        )


def _make_citation(
    *, context_id: str, chunk_id: str, block: str = "block-x"
) -> ArticleRagAskContextCitation:
    return ArticleRagAskContextCitation(
        context_id=context_id,
        chunk_id=chunk_id,
        citation={
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
    )


def _make_bundle(
    *,
    prompt_text: str = _PROMPT_TEXT,
    citations: tuple[ArticleRagAskContextCitation, ...] | None = None,
    context_ids: tuple[str, ...] | None = None,
    omitted_hit_count: int = 0,
    budget_exceeded: bool = False,
    source_pack_hash: str = _SOURCE_PACK_HASH,
) -> ArticleRagAskContextBundle:
    citations = citations or (
        _make_citation(context_id="rag-1", chunk_id="c1"),
        _make_citation(context_id="rag-2", chunk_id="c2"),
    )
    context_ids = context_ids or ("rag-1", "rag-2")
    return ArticleRagAskContextBundle(
        prompt_context_text=prompt_text,
        citations=citations,
        context_ids=context_ids,
        source_pack_hash=source_pack_hash,
        omitted_hit_count=omitted_hit_count,
        budget_exceeded=budget_exceeded,
        empty=False,
        reading_record_id=_RECORD_ID,
        stable_document_id=_STABLE_DOC_ID,
        base_id=_BASE_ID,
        record_generation=1,
        plan_content_sha256=_PLAN_HASH,
    )


def _make_resolver_result(
    *,
    status: str = "available",
    bundle: ArticleRagAskContextBundle | None = None,
    enabled: bool = True,
    failure_code: str | None = None,
    retryable: bool = False,
    fallback_allowed: bool = True,
    query_sha256: str | None = None,
    omitted_hit_count: int | None = None,
    budget_exceeded: bool | None = None,
) -> ArticleRagAskContextResolveResult:
    if query_sha256 is None:
        query_sha256 = hashlib.sha256(b"hello").hexdigest()
    return ArticleRagAskContextResolveResult(
        status=status,
        enabled=enabled,
        bundle=bundle,
        failure_code=failure_code,
        retryable=retryable,
        fallback_allowed=fallback_allowed,
        reading_record_id=_RECORD_ID,
        query_sha256=query_sha256,
        omitted_hit_count=omitted_hit_count,
        budget_exceeded=budget_exceeded,
        stable_document_id=_STABLE_DOC_ID,
        base_id=_BASE_ID,
        record_generation=1,
        plan_content_sha256=_PLAN_HASH,
    )


def _build_service(
    *,
    resolver: _FakeResolver | None = None,
) -> ArticleRagAskPromptAttachmentService:
    return ArticleRagAskPromptAttachmentService(
        resolver=resolver
        or _FakeResolver(
            result_factory=lambda **kw: _make_resolver_result()
        )
    )


# ---------------------------------------------------------------------------
# 1. Available path
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_available_includes_context_and_citations() -> None:
    bundle = _make_bundle()
    resolver = _FakeResolver(
        result_factory=lambda **kw: _make_resolver_result(
            status="available", bundle=bundle,
            omitted_hit_count=0, budget_exceeded=False,
        )
    )
    service = _build_service(resolver=resolver)
    attachment = await service.build_for_ask(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
    assert isinstance(attachment, ArticleRagAskPromptAttachment)
    assert attachment.status == "available"
    assert attachment.enabled is True
    assert attachment.should_include_context is True
    assert attachment.fallback_allowed is True
    # prompt_context_text is exactly the composer's output.
    assert attachment.prompt_context_text == _PROMPT_TEXT
    # citations are structured and in score-descending order.
    assert [c["context_id"] for c in attachment.citations] == [
        "rag-1",
        "rag-2",
    ]
    assert attachment.context_ids == ("rag-1", "rag-2")
    # source_pack_hash is propagated.
    assert attachment.source_pack_hash == _SOURCE_PACK_HASH
    # Stable ids propagated.
    assert attachment.reading_record_id == _RECORD_ID
    assert attachment.stable_document_id == _STABLE_DOC_ID
    assert attachment.base_id == _BASE_ID
    assert attachment.record_generation == 1
    assert attachment.plan_content_sha256 == _PLAN_HASH
    assert attachment.query_sha256 == hashlib.sha256(b"hello").hexdigest()
    # Budget / omitted are echoed.
    assert attachment.omitted_hit_count == 0
    assert attachment.budget_exceeded is False
    # OK path: failure_code is None, retryable is False.
    assert attachment.failure_code is None
    assert attachment.retryable is False


# ---------------------------------------------------------------------------
# 2. Non-OK paths all produce no-context attachments
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_disabled_path_no_context() -> None:
    resolver = _FakeResolver(
        result_factory=lambda **kw: _make_resolver_result(
            status="disabled",
            enabled=False,
            bundle=None,
            failure_code=None,
        )
    )
    service = _build_service(resolver=resolver)
    attachment = await service.build_for_ask(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
        enabled=False,
    )
    assert attachment.status == "disabled"
    assert attachment.enabled is False
    assert attachment.should_include_context is False
    assert attachment.fallback_allowed is True
    assert attachment.prompt_context_text == ""
    assert attachment.citations == ()
    assert attachment.context_ids == ()
    assert attachment.source_pack_hash is None


@pytest.mark.anyio
async def test_empty_path_no_context() -> None:
    resolver = _FakeResolver(
        result_factory=lambda **kw: _make_resolver_result(
            status="empty", bundle=None, failure_code=None
        )
    )
    service = _build_service(resolver=resolver)
    attachment = await service.build_for_ask(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
    assert attachment.status == "empty"
    assert attachment.should_include_context is False
    assert attachment.fallback_allowed is True
    assert attachment.prompt_context_text == ""


@pytest.mark.anyio
async def test_not_indexed_path_no_context() -> None:
    resolver = _FakeResolver(
        result_factory=lambda **kw: _make_resolver_result(
            status="not_indexed_or_unavailable",
            bundle=None,
            failure_code="context_no_indexed_run",
            retryable=False,
        )
    )
    service = _build_service(resolver=resolver)
    attachment = await service.build_for_ask(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
    assert attachment.status == "not_indexed_or_unavailable"
    assert attachment.should_include_context is False
    assert attachment.fallback_allowed is True
    assert attachment.prompt_context_text == ""
    # The upstream failure_code is preserved.
    assert attachment.failure_code == "context_no_indexed_run"


@pytest.mark.anyio
async def test_composer_rejected_path_no_context() -> None:
    resolver = _FakeResolver(
        result_factory=lambda **kw: _make_resolver_result(
            status="composer_rejected",
            bundle=None,
            failure_code="ask_context_empty_text",
            retryable=False,
        )
    )
    service = _build_service(resolver=resolver)
    attachment = await service.build_for_ask(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
    assert attachment.status == "composer_rejected"
    assert attachment.should_include_context is False
    assert attachment.fallback_allowed is True
    assert attachment.prompt_context_text == ""
    assert attachment.citations == ()
    # The upstream composer failure_code is preserved.
    assert attachment.failure_code == "ask_context_empty_text"


# ---------------------------------------------------------------------------
# 3. Unexpected resolver exception
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_unexpected_resolver_exception_wrapped_fail_soft() -> None:
    secret = "SECRET-QUERY-DO-NOT-LEAK-DO-NOT-LEAK"
    secret_message = (
        f"resolver exploded with internal diagnostics: {secret}"
    )
    resolver = _FakeResolver(raise_exc=RuntimeError(secret_message))
    service = _build_service(resolver=resolver)
    # MUST NOT raise.
    attachment = await service.build_for_ask(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text=secret,
    )
    assert attachment.status == "not_indexed_or_unavailable"
    assert attachment.should_include_context is False
    assert attachment.fallback_allowed is True
    assert attachment.prompt_context_text == ""
    assert attachment.citations == ()
    assert attachment.failure_code == FAILURE_CODE_ATTACHMENT_UNEXPECTED_ERROR
    # The original message MUST NOT leak anywhere in the attachment.
    assert secret_message not in repr(attachment)
    assert secret_message not in str(attachment)
    # The query text MUST NOT leak.
    assert secret not in repr(attachment)
    assert secret not in str(attachment)


# ---------------------------------------------------------------------------
# 4. provider_metadata never on the attachment
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_provider_metadata_never_on_attachment() -> None:
    """Even if the upstream resolver or bundle had a
    ``provider_metadata`` field, the attachment MUST NOT carry it.
    """
    resolver = _FakeResolver(
        result_factory=lambda **kw: _make_resolver_result(
            status="available", bundle=_make_bundle()
        )
    )
    service = _build_service(resolver=resolver)
    attachment = await service.build_for_ask(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
    # No provider_metadata attribute at all.
    assert not hasattr(attachment, "provider_metadata")
    # Repr / str MUST NOT contain any searcher diagnostic value.
    for forbidden in ("zilliz", "latency_ms", "region"):
        assert forbidden not in repr(attachment)


# ---------------------------------------------------------------------------
# 5. prompt_context_text is exactly the composer's output
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_prompt_context_text_not_mutated() -> None:
    """The attachment MUST NOT annotate the prompt text with
    citation JSON, projection keys, or any other metadata.  The
    text the LLM sees is exactly the composer's
    ``prompt_context_text``."""
    resolver = _FakeResolver(
        result_factory=lambda **kw: _make_resolver_result(
            status="available", bundle=_make_bundle()
        )
    )
    service = _build_service(resolver=resolver)
    attachment = await service.build_for_ask(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
    assert attachment.prompt_context_text == _PROMPT_TEXT
    # The text MUST NOT have any forbidden projection keys
    # appended / interleaved.
    for forbidden in (
        "plate",
        "markdown",
        "dom",
        "slate",
        "ui",
        "render",
        "html",
        "json",
    ):
        assert forbidden not in attachment.prompt_context_text.lower(), (
            f"forbidden substring {forbidden!r} appears in "
            f"prompt_context_text"
        )


# ---------------------------------------------------------------------------
# 6. citations are structured and separate
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_citations_structured_and_score_descending() -> None:
    citations = (
        _make_citation(context_id="rag-1", chunk_id="c1", block="block-1"),
        _make_citation(context_id="rag-2", chunk_id="c2", block="block-2"),
        _make_citation(context_id="rag-3", chunk_id="c3", block="block-3"),
    )
    bundle = _make_bundle(
        citations=citations,
        context_ids=("rag-1", "rag-2", "rag-3"),
    )
    resolver = _FakeResolver(
        result_factory=lambda **kw: _make_resolver_result(
            status="available", bundle=bundle
        )
    )
    service = _build_service(resolver=resolver)
    attachment = await service.build_for_ask(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
    assert [c["context_id"] for c in attachment.citations] == [
        "rag-1",
        "rag-2",
        "rag-3",
    ]
    # Citation dicts are preserved verbatim.
    for src, dst in zip(citations, attachment.citations):
        assert dst["chunk_id"] == src.chunk_id
        assert dst["citation"] == src.citation
    # Citations are NOT in the prompt text (we do not parse
    # them back from the text).
    for c in attachment.citations:
        assert f'chunk_id={c["chunk_id"]!r}' not in attachment.prompt_context_text
        assert f'"block_ids": ["{c["citation"]["block_ids"][0]}"]' not in attachment.prompt_context_text


# ---------------------------------------------------------------------------
# 7. Malformed resolver result handling
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_malformed_available_with_no_bundle_fails_soft() -> None:
    """A hostile / regressed resolver could return
    ``status="available"`` with ``bundle=None``.  The contract
    says the attachment must fail soft (NOT include context)
    rather than crash.
    """

    class _MalformedResolver:
        async def resolve_for_record(self, **kwargs: Any) -> Any:
            # Contract violation: status=available but bundle is
            # None.
            return ArticleRagAskContextResolveResult(
                status="available",
                enabled=True,
                bundle=None,
                failure_code=None,
                retryable=False,
                fallback_allowed=True,
                reading_record_id=_RECORD_ID,
                query_sha256=hashlib.sha256(b"x").hexdigest(),
            )

    service = ArticleRagAskPromptAttachmentService(resolver=_MalformedResolver())
    attachment = await service.build_for_ask(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
    assert attachment.status == "not_indexed_or_unavailable"
    assert attachment.should_include_context is False
    assert attachment.fallback_allowed is True
    assert attachment.prompt_context_text == ""
    assert attachment.citations == ()


@pytest.mark.anyio
async def test_malformed_available_with_empty_prompt_text_fails_soft() -> None:
    """A hostile / regressed resolver could return
    ``status="available"`` with a bundle whose
    ``prompt_context_text`` is empty.  The contract says fail
    soft.
    """

    class _EmptyTextBundleResolver:
        async def resolve_for_record(self, **kwargs: Any) -> Any:
            return ArticleRagAskContextResolveResult(
                status="available",
                enabled=True,
                bundle=_make_bundle(prompt_text=""),
                failure_code=None,
                retryable=False,
                fallback_allowed=True,
                reading_record_id=_RECORD_ID,
                query_sha256=hashlib.sha256(b"x").hexdigest(),
            )

    service = ArticleRagAskPromptAttachmentService(
        resolver=_EmptyTextBundleResolver()
    )
    attachment = await service.build_for_ask(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
    assert attachment.should_include_context is False
    assert attachment.fallback_allowed is True
    assert attachment.prompt_context_text == ""


@pytest.mark.anyio
async def test_malformed_non_resolver_result_object_fails_soft() -> None:
    """A regression where the resolver returns a non-dataclass
    object (e.g. a bare dict) must fail soft, not crash."""

    class _BrokenResolver:
        async def resolve_for_record(self, **kwargs: Any) -> Any:
            # Return a non-ArticleRagAskContextResolveResult.
            return {"status": "available", "bundle": None}

    service = ArticleRagAskPromptAttachmentService(resolver=_BrokenResolver())
    # MUST NOT raise.
    attachment = await service.build_for_ask(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
    assert attachment.status == "not_indexed_or_unavailable"
    assert attachment.should_include_context is False
    assert attachment.failure_code == FAILURE_CODE_ATTACHMENT_UNEXPECTED_ERROR


# ---------------------------------------------------------------------------
# 8. Missing resolver config
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_missing_resolver_fails_soft() -> None:
    service = ArticleRagAskPromptAttachmentService()  # no resolver
    attachment = await service.build_for_ask(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
    assert attachment.status == "not_indexed_or_unavailable"
    assert attachment.should_include_context is False
    assert attachment.failure_code == FAILURE_CODE_ATTACHMENT_UNEXPECTED_ERROR
    assert attachment.fallback_allowed is True
    assert attachment.prompt_context_text == ""


# ---------------------------------------------------------------------------
# 9. Parameter passthrough to resolver
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_parameter_passthrough_to_resolver() -> None:
    resolver = _FakeResolver(
        result_factory=lambda **kw: _make_resolver_result(
            status="available", bundle=_make_bundle()
        )
    )
    service = _build_service(resolver=resolver)
    await service.build_for_ask(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
        enabled=False,
        limit=12,
        max_context_chars=8000,
    )
    assert len(resolver.calls) == 1
    call = resolver.calls[0]
    assert call["enabled"] is False
    assert call["limit"] == 12
    assert call["max_context_chars"] == 8000
    assert call["reading_record_id"] == str(_RECORD_ID)
    assert call["query_text"] == "hello"


# ---------------------------------------------------------------------------
# 10. query_text never appears in repr/str
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_query_text_not_in_repr_or_str() -> None:
    secret = "SECRET-QUERY-DO-NOT-LEAK-DO-NOT-LEAK"
    resolver = _FakeResolver(
        result_factory=lambda **kw: _make_resolver_result(
            status="available", bundle=_make_bundle(),
            query_sha256=hashlib.sha256(secret.encode("utf-8")).hexdigest(),
        )
    )
    service = _build_service(resolver=resolver)
    attachment = await service.build_for_ask(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text=secret,
    )
    assert secret not in repr(attachment)
    assert secret not in str(attachment)


# ---------------------------------------------------------------------------
# 11. Stable ids + budget fields preserved when available
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_stable_ids_preserved_on_no_context_paths() -> None:
    """The non-OK paths MUST still echo stable ids (so the Ask
    layer can use them for cache keys / log dedup)."""
    resolver = _FakeResolver(
        result_factory=lambda **kw: _make_resolver_result(
            status="empty", bundle=None,
            omitted_hit_count=3, budget_exceeded=True,
        )
    )
    service = _build_service(resolver=resolver)
    attachment = await service.build_for_ask(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
    assert attachment.status == "empty"
    assert attachment.reading_record_id == _RECORD_ID
    assert attachment.stable_document_id == _STABLE_DOC_ID
    assert attachment.base_id == _BASE_ID
    assert attachment.record_generation == 1
    assert attachment.plan_content_sha256 == _PLAN_HASH
    # Budget / omitted are echoed even on the no-context paths
    # (they came from the resolver's plan, not the bundle).
    assert attachment.omitted_hit_count == 3
    assert attachment.budget_exceeded is True


# ---------------------------------------------------------------------------
# 12. Budget / omitted are echoed on available path
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_budget_and_omitted_echoed_on_available() -> None:
    bundle = _make_bundle()
    resolver = _FakeResolver(
        result_factory=lambda **kw: _make_resolver_result(
            status="available", bundle=bundle,
            omitted_hit_count=4, budget_exceeded=True,
        )
    )
    service = _build_service(resolver=resolver)
    attachment = await service.build_for_ask(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
    assert attachment.omitted_hit_count == 4
    assert attachment.budget_exceeded is True


# ---------------------------------------------------------------------------
# 13. Determinism
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_attachment_deterministic_for_same_input() -> None:
    resolver_factory = lambda: _FakeResolver(
        result_factory=lambda **kw: _make_resolver_result(
            status="available", bundle=_make_bundle()
        )
    )
    service_a = _build_service(resolver=resolver_factory())
    service_b = _build_service(resolver=resolver_factory())
    a1 = await service_a.build_for_ask(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
    a2 = await service_b.build_for_ask(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
    assert a1.prompt_context_text == a2.prompt_context_text
    assert a1.citations == a2.citations
    assert a1.context_ids == a2.context_ids
    assert a1.source_pack_hash == a2.source_pack_hash
    assert a1.query_sha256 == a2.query_sha256
    assert a1.status == a2.status


# ---------------------------------------------------------------------------
# 14. Constants
# ---------------------------------------------------------------------------


def test_default_constants() -> None:
    assert DEFAULT_ATTACHMENT_LIMIT == 8
    assert DEFAULT_ATTACHMENT_MAX_CONTEXT_CHARS == 4000


def test_failure_code_constant() -> None:
    assert (
        FAILURE_CODE_ATTACHMENT_UNEXPECTED_ERROR
        == "article_rag_prompt_attachment_unexpected_error"
    )


# ---------------------------------------------------------------------------
# 15. Disabled path: resolver NOT called (resolver-level short-circuit)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_disabled_consults_resolver_but_resolver_short_circuits() -> (
    None
):
    """The attachment service does NOT short-circuit at the
    resolver call site — it always delegates to the resolver
    (which itself has the ``enabled=False`` short-circuit).  The
    resolver's disabled result is what the attachment observes.
    """
    resolver = _FakeResolver(
        result_factory=lambda **kw: _make_resolver_result(
            status="disabled",
            enabled=False,
            bundle=None,
        )
    )
    service = _build_service(resolver=resolver)
    await service.build_for_ask(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
        enabled=False,
    )
    # The resolver WAS called (the attachment does not short-
    # circuit — the resolver does).
    assert len(resolver.calls) == 1
    assert resolver.calls[0]["enabled"] is False


# ---------------------------------------------------------------------------
# 16. Reviewer fixes: runtime status guard + citation allowlist
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_unknown_resolver_status_fails_soft() -> None:
    """Reviewer P1 fix: ``ArticleRagAskContextResolveStatus`` is
    a ``typing.Literal`` (compile-time only).  A regression in
    the resolver (or a hostile fake in a test) could surface
    an unrecognised status string (e.g. ``"paused"``,
    ``"failed"``, ``""``).  The Ask layer keys its fallback
    policy on the status literal — an unknown value would
    silently break the dispatch contract.

    The attachment MUST fail-soft to
    ``status="not_indexed_or_unavailable"`` with
    ``failure_code=FAILURE_CODE_ATTACHMENT_UNEXPECTED_ERROR`` so
    the Ask layer's default branch still works.
    """

    class _HostileStatusResolver:
        async def resolve_for_record(self, **kwargs: Any) -> Any:
            return ArticleRagAskContextResolveResult(
                # Deliberately an unrecognised status string.
                status="paused",
                enabled=True,
                bundle=_make_bundle(),
                failure_code=None,
                retryable=False,
                fallback_allowed=True,
                reading_record_id=_RECORD_ID,
                query_sha256=hashlib.sha256(b"x").hexdigest(),
            )

    service = ArticleRagAskPromptAttachmentService(
        resolver=_HostileStatusResolver()
    )
    attachment = await service.build_for_ask(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
    assert attachment.status == "not_indexed_or_unavailable"
    assert attachment.should_include_context is False
    assert attachment.fallback_allowed is True
    assert attachment.prompt_context_text == ""
    assert attachment.citations == ()
    assert attachment.context_ids == ()
    assert attachment.source_pack_hash is None
    assert attachment.failure_code == FAILURE_CODE_ATTACHMENT_UNEXPECTED_ERROR


@pytest.mark.anyio
async def test_empty_string_resolver_status_fails_soft() -> None:
    """A regression returning ``status=""`` (an unrecognised
    string) must also fail-soft."""

    class _EmptyStatusResolver:
        async def resolve_for_record(self, **kwargs: Any) -> Any:
            return ArticleRagAskContextResolveResult(
                status="",
                enabled=True,
                bundle=None,
                failure_code=None,
                retryable=False,
                fallback_allowed=True,
                reading_record_id=_RECORD_ID,
                query_sha256=hashlib.sha256(b"x").hexdigest(),
            )

    service = ArticleRagAskPromptAttachmentService(
        resolver=_EmptyStatusResolver()
    )
    attachment = await service.build_for_ask(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
    assert attachment.status == "not_indexed_or_unavailable"
    assert attachment.failure_code == FAILURE_CODE_ATTACHMENT_UNEXPECTED_ERROR


@pytest.mark.anyio
async def test_hostile_citation_dict_strips_non_i4a_keys() -> None:
    """Reviewer P2 fix: a regression / hostile fake in the
    I4E / I4F / I4G chain could put provider_metadata, the
    query text, or a UI projection key on a citation dict.  The
    attachment's citation allowlist (the 9 I4A citation truth
    keys) MUST strip every other key — the attachment MUST NOT
    surface provider / query / projection fields.

    Specifically:
      * the citation dict on the attachment contains ONLY the
        9 I4A keys (no extras);
      * a secret value injected via a non-allowlisted key does
        not appear anywhere in the attachment;
      * a non-allowlisted key whose value is the citation truth
        key (e.g. ``"block_ids": "token=SECRET"``) is dropped
        entirely (we do not re-scrub values, only keys).
    """
    secret = "SECRET-INJECTED-VIA-HOSTILE-CITATION-DO-NOT-LEAK"
    secret_query = "SECRET-QUERY-DO-NOT-LEAK-DO-NOT-LEAK"
    hostile_citation = {
        # The 9 I4A truth keys — these MUST survive.
        "reading_record_id": str(_RECORD_ID),
        "stable_document_id": str(_STABLE_DOC_ID),
        "base_id": str(_BASE_ID),
        "record_generation": 1,
        "block_ids": ["block-x"],
        "unit_ids": [],
        "anchor_segment_ids": [],
        "canonical_text_start_utf16": 0,
        "canonical_text_end_utf16": 10,
        # Hostile keys — these MUST be stripped.
        "provider_metadata": {"provider": "zilliz", "token": secret},
        "query_text": secret_query,
        "query": secret_query,
        "query_vector": [0.1, 0.2, 0.3],
        "token": secret,
        "uri": "https://secret.zilliz.example.com",
        "secret": secret,
        "plate": {"op": "slate"},
        "markdown": "**hello**",
        "dom": {"tag": "div"},
        "slate": {"path": [0, 1]},
        "ui": {"display": "x"},
        "render_profile": "v1",
        "html": "<p>x</p>",
        "text": "SECRET-CHUNK-TEXT",
    }
    citation = ArticleRagAskContextCitation(
        context_id="rag-1",
        chunk_id="c1",
        citation=hostile_citation,
    )
    bundle = _make_bundle(
        citations=(citation,),
        context_ids=("rag-1",),
    )
    resolver = _FakeResolver(
        result_factory=lambda **kw: _make_resolver_result(
            status="available", bundle=bundle
        )
    )
    service = _build_service(resolver=resolver)
    attachment = await service.build_for_ask(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text=secret_query,
    )
    # OK path: context is included.
    assert attachment.should_include_context is True
    assert len(attachment.citations) == 1
    cit = attachment.citations[0]
    # Citation dict contains ONLY the 9 I4A keys.
    assert set(cit["citation"].keys()) == {
        "reading_record_id",
        "stable_document_id",
        "base_id",
        "record_generation",
        "block_ids",
        "unit_ids",
        "anchor_segment_ids",
        "canonical_text_start_utf16",
        "canonical_text_end_utf16",
    }
    # The 9 I4A values are preserved.
    assert cit["citation"]["reading_record_id"] == str(_RECORD_ID)
    assert cit["citation"]["block_ids"] == ["block-x"]
    # Defence in depth: the secret strings MUST NOT appear
    # anywhere in the attachment (repr / str / prompt text /
    # citation).
    assert secret not in repr(attachment)
    assert secret not in str(attachment)
    assert secret not in attachment.prompt_context_text
    assert secret_query not in repr(attachment)
    assert secret_query not in str(attachment)
    # The hostile key NAMES (when they appear in repr/str) and
    # the hostile key VALUES (provider_metadata sub-dict content,
    # etc.) MUST NOT appear.  We check the most distinctive
    # ones; ``text`` is excluded because the substring "text"
    # appears in the attachment field name
    # ``prompt_context_text`` (legitimate, not a leak).
    for hostile_key in (
        "provider_metadata",
        "query_text",
        "query_vector",
        "token",
        "uri",
        "secret",
        "plate",
        "markdown",
        "dom",
        "slate",
        "ui",
        "render_profile",
        "html",
    ):
        assert hostile_key not in repr(attachment)
        assert hostile_key not in str(attachment)


@pytest.mark.anyio
async def test_citation_allowlist_preserves_i4a_keys() -> None:
    """Positive control: a well-formed 9-key I4A citation dict
    is preserved verbatim.
    """
    i4a_citation = {
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
    citation = ArticleRagAskContextCitation(
        context_id="rag-1",
        chunk_id="c1",
        citation=i4a_citation,
    )
    bundle = _make_bundle(
        citations=(citation,),
        context_ids=("rag-1",),
    )
    resolver = _FakeResolver(
        result_factory=lambda **kw: _make_resolver_result(
            status="available", bundle=bundle
        )
    )
    service = _build_service(resolver=resolver)
    attachment = await service.build_for_ask(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
    assert attachment.citations[0]["citation"] == i4a_citation


@pytest.mark.anyio
async def test_citation_allowlist_drops_unrecognised_i4a_keys() -> None:
    """A citation dict with a typo'd I4A key (e.g. ``block_id``
    instead of ``block_ids``) is dropped — only the canonical
    9 keys survive.  This pins the allowlist semantics.
    """
    partial_citation = {
        "reading_record_id": str(_RECORD_ID),
        "stable_document_id": str(_STABLE_DOC_ID),
        "base_id": str(_BASE_ID),
        "record_generation": 1,
        # Typo'd singular — NOT in the allowlist.
        "block_id": ["block-x"],
        # Correct plural — MUST be in the allowlist.
        "block_ids": ["block-correct"],
    }
    citation = ArticleRagAskContextCitation(
        context_id="rag-1",
        chunk_id="c1",
        citation=partial_citation,
    )
    bundle = _make_bundle(
        citations=(citation,),
        context_ids=("rag-1",),
    )
    resolver = _FakeResolver(
        result_factory=lambda **kw: _make_resolver_result(
            status="available", bundle=bundle
        )
    )
    service = _build_service(resolver=resolver)
    attachment = await service.build_for_ask(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
    cit = attachment.citations[0]["citation"]
    # ``block_ids`` survives; the typo'd ``block_id`` is dropped.
    assert cit.get("block_ids") == ["block-correct"]
    assert "block_id" not in cit