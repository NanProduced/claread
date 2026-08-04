# task-history: D6-I4H (renamed from test_d6_i4h_article_rag_ask_context_resolver.py)
"""D6-I4H: tests for Article RAG ask context resolver.

Covers:
  * disabled short-circuit — no service call, no composer call.
  * available happy path — context service + composer both called,
    bundle surfaces.
  * empty pack → status="empty".
  * context service typed error → status="not_indexed_or_unavailable"
    with original failure_code + retryable preserved; no exception
    raised.
  * composer typed error → status="composer_rejected" with original
    failure_code; no exception raised.
  * unexpected context service exception → not_indexed_or_unavailable
    with FAILURE_CODE_RESOLVER_UNEXPECTED_ERROR; original message
    NOT leaked in result.
  * unexpected composer exception → composer_rejected with
    FAILURE_CODE_RESOLVER_UNEXPECTED_ERROR; original message NOT
    leaked in result.
  * query_sha256 deterministic, never contains query_text.
  * parameter passthrough: limit / max_context_chars
    reach the context service.
  * result never carries provider_metadata.
  * missing context_service / composer config → fail-soft with
    FAILURE_CODE_RESOLVER_UNEXPECTED_ERROR.
  * fallback_allowed=True for every non-OK status.
  * no DB / LLM / network.

No real DB / Zilliz / DashScope / LLM.  Uses FakeContextService +
FakeComposer with recording ``calls`` attributes.
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
    ArticleRagAskContextComposerError,
    FAILURE_CODE_ASK_CONTEXT_EMPTY_TEXT,
)
from app.services.reader_orchestration.article_rag_ask_context_resolver import (
    DEFAULT_RESOLVER_LIMIT,
    DEFAULT_RESOLVER_MAX_CONTEXT_CHARS,
    FAILURE_CODE_RESOLVER_COMPOSER_REJECTED,
    FAILURE_CODE_RESOLVER_DISABLED,
    FAILURE_CODE_RESOLVER_UNEXPECTED_ERROR,
    ArticleRagAskContextResolveResult,
    ArticleRagAskContextResolver,
)
from app.services.reader_orchestration.article_rag_context_service import (
    ArticleRagContextItem,
    ArticleRagContextPack,
    ArticleRagContextServiceError,
    FAILURE_CODE_CONTEXT_RETRIEVAL_FAILED,
)
from app.services.reader_orchestration.article_rag_index_worker import (
    ArticleRagIndexWorkerError,
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
_USER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
_STABLE_DOC_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
_BASE_ID = uuid.UUID("44444444-4444-4444-4444-444444444444")
_PLAN_HASH = "abc123def456" + "f" * 52


@dataclass
class _FakeContextService:
    """Stand-in for :class:`ArticleRagContextService`.

    Configure either ``pack_factory`` (happy path) or ``raise_exc``
    (error path) — never both.  ``raise_exc`` takes precedence.
    Records every call.
    """

    calls: list[dict[str, Any]] = field(default_factory=list)
    pack_factory: "callable | None" = None
    raise_exc: Exception | None = None

    async def build_context_pack_for_record(
        self,
        *,
        reading_record_id: uuid.UUID,
        user_id: uuid.UUID,
        query_text: str,
        limit: int = DEFAULT_RESOLVER_LIMIT,
        max_context_chars: int = DEFAULT_RESOLVER_MAX_CONTEXT_CHARS,
    ) -> ArticleRagContextPack:
        self.calls.append(
            {
                "reading_record_id": str(reading_record_id),
                "user_id": str(user_id),
                "query_text": query_text,
                "limit": int(limit),
                "max_context_chars": int(max_context_chars),
            }
        )
        if self.raise_exc is not None:
            raise self.raise_exc
        assert self.pack_factory is not None
        return self.pack_factory(reading_record_id=reading_record_id)


@dataclass
class _FakeComposer:
    """Stand-in for :class:`ArticleRagAskContextComposer`."""

    calls: list[dict[str, Any]] = field(default_factory=list)
    bundle_factory: "callable | None" = None
    raise_exc: Exception | None = None

    def compose(
        self, pack: ArticleRagContextPack
    ) -> ArticleRagAskContextBundle:
        self.calls.append(
            {
                "item_count": len(pack.items),
                "query_sha256": pack.query_sha256,
            }
        )
        if self.raise_exc is not None:
            raise self.raise_exc
        assert self.bundle_factory is not None
        return self.bundle_factory(pack=pack)


def _make_item(
    *,
    context_id: str = "rag-1",
    rank: int = 1,
    chunk_id: str = "c1",
    text: str = "alpha",
    score: float = 0.9,
) -> ArticleRagContextItem:
    return ArticleRagContextItem(
        context_id=context_id,
        rank=rank,
        chunk_id=chunk_id,
        score=score,
        text=text,
        citation={
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
        metadata_json={"block_type": "paragraph"},
    )


def _make_pack(
    *,
    items: list[ArticleRagContextItem] | None = None,
    query_sha256: str | None = None,
    provider_metadata: dict[str, Any] | None = None,
    omitted_hit_count: int = 0,
    budget_exceeded: bool = False,
) -> ArticleRagContextPack:
    if items is None:
        items = [_make_item()]
    if query_sha256 is None:
        query_sha256 = hashlib.sha256(b"hello").hexdigest()
    return ArticleRagContextPack(
        reading_record_id=_RECORD_ID,
        stable_document_id=_STABLE_DOC_ID,
        base_id=_BASE_ID,
        record_generation=1,
        plan_content_sha256=_PLAN_HASH,
        query_sha256=query_sha256,
        items=tuple(items),
        total_text_chars=sum(len(it.text) for it in items),
        omitted_hit_count=omitted_hit_count,
        budget_exceeded=budget_exceeded,
        max_context_chars=4000,
        provider_metadata=provider_metadata
        or {"provider": "zilliz"},
    )


def _make_bundle(pack: ArticleRagContextPack) -> ArticleRagAskContextBundle:
    return ArticleRagAskContextBundle(
        prompt_context_text="[rag-1] rank=1 score=0.900000\nalpha",
        citations=(),
        context_ids=("rag-1",),
        source_pack_hash="abc" + "f" * 61,
        omitted_hit_count=pack.omitted_hit_count,
        budget_exceeded=pack.budget_exceeded,
        empty=False,
        reading_record_id=pack.reading_record_id,
        stable_document_id=pack.stable_document_id,
        base_id=pack.base_id,
        record_generation=pack.record_generation,
        plan_content_sha256=pack.plan_content_sha256,
    )


def _build_resolver(
    *,
    context_service: _FakeContextService | None = None,
    composer: _FakeComposer | None = None,
) -> ArticleRagAskContextResolver:
    return ArticleRagAskContextResolver(
        context_service=context_service
        or _FakeContextService(
            pack_factory=lambda **kw: _make_pack()
        ),
        composer=composer
        or _FakeComposer(bundle_factory=lambda pack: _make_bundle(pack)),
    )


# ---------------------------------------------------------------------------
# 1. Disabled short-circuit
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_disabled_short_circuits_no_service_call() -> None:
    context_service = _FakeContextService(
        pack_factory=lambda **kw: _make_pack()
    )
    composer = _FakeComposer(bundle_factory=lambda pack: _make_bundle(pack))
    resolver = _build_resolver(
        context_service=context_service, composer=composer
    )
    result = await resolver.resolve_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
        enabled=False,
    )
    assert isinstance(result, ArticleRagAskContextResolveResult)
    assert result.status == "disabled"
    assert result.enabled is False
    assert result.bundle is None
    assert result.failure_code == FAILURE_CODE_RESOLVER_DISABLED
    assert result.retryable is False
    assert result.fallback_allowed is True
    # The context service MUST NOT have been called.
    assert context_service.calls == []
    # The composer MUST NOT have been called.
    assert composer.calls == []


@pytest.mark.anyio
async def test_disabled_still_computes_query_sha256() -> None:
    """Even when disabled, query_sha256 is computed so the Ask
    layer can use it for log dedup / cache keys.
    """
    context_service = _FakeContextService(
        pack_factory=lambda **kw: _make_pack()
    )
    resolver = _build_resolver(context_service=context_service)
    result = await resolver.resolve_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello world",
        enabled=False,
    )
    assert result.query_sha256 == hashlib.sha256(
        b"hello world"
    ).hexdigest()
    # The raw query text MUST NOT appear anywhere in the result.
    assert "hello world" not in repr(result)


# ---------------------------------------------------------------------------
# 2. Available happy path
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_available_happy_path() -> None:
    pack = _make_pack()
    context_service = _FakeContextService(
        pack_factory=lambda **kw: pack
    )
    composer = _FakeComposer(bundle_factory=lambda pack: _make_bundle(pack))
    resolver = _build_resolver(
        context_service=context_service, composer=composer
    )
    result = await resolver.resolve_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
    )
    assert result.status == "available"
    assert result.enabled is True
    assert result.bundle is not None
    assert result.failure_code is None
    assert result.retryable is False
    assert result.fallback_allowed is True
    # Both the context service and the composer were called.
    assert len(context_service.calls) == 1
    assert len(composer.calls) == 1
    # Stable ids are surfaced.
    assert result.stable_document_id == _STABLE_DOC_ID
    assert result.base_id == _BASE_ID
    assert result.record_generation == 1
    assert result.plan_content_sha256 == _PLAN_HASH
    assert result.reading_record_id == _RECORD_ID


@pytest.mark.anyio
async def test_parameter_passthrough_to_context_service() -> None:
    context_service = _FakeContextService(
        pack_factory=lambda **kw: _make_pack()
    )
    resolver = _build_resolver(context_service=context_service)
    await resolver.resolve_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
        limit=12,
        max_context_chars=8000,
    )
    assert len(context_service.calls) == 1
    call = context_service.calls[0]
    assert call["limit"] == 12
    assert call["max_context_chars"] == 8000
    assert call["reading_record_id"] == str(_RECORD_ID)
    assert call["user_id"] == str(_USER_ID)
    assert call["query_text"] == "hello"


# ---------------------------------------------------------------------------
# 3. Empty pack
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_empty_pack_returns_status_empty() -> None:
    pack = _make_pack(items=[])
    context_service = _FakeContextService(
        pack_factory=lambda **kw: pack
    )
    composer = _FakeComposer(bundle_factory=lambda pack: _make_bundle(pack))
    resolver = _build_resolver(
        context_service=context_service, composer=composer
    )
    result = await resolver.resolve_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
    )
    assert result.status == "empty"
    assert result.bundle is None
    assert result.fallback_allowed is True
    assert result.failure_code is None
    assert result.retryable is False
    # The composer MUST NOT have been called for an empty pack
    # (the resolver short-circuits before calling it).
    assert composer.calls == []
    # The context service WAS called; the empty status is decided
    # by the resolver based on pack.items.
    assert len(context_service.calls) == 1


# ---------------------------------------------------------------------------
# 4. Context service typed error → not_indexed_or_unavailable
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_context_service_typed_error_not_indexed() -> None:
    context_service = _FakeContextService(
        raise_exc=ArticleRagContextServiceError(
            "no indexed run for stable_document_id=...; refusing to "
            "call the embedding provider",
            retryable=False,
            failure_code=FAILURE_CODE_CONTEXT_RETRIEVAL_FAILED,
        )
    )
    composer = _FakeComposer(bundle_factory=lambda pack: _make_bundle(pack))
    resolver = _build_resolver(
        context_service=context_service, composer=composer
    )
    # The resolver MUST NOT raise.
    result = await resolver.resolve_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
    )
    assert result.status == "not_indexed_or_unavailable"
    assert result.bundle is None
    # The original failure_code is preserved (so dashboards can
    # dispatch on the actual cause).
    assert result.failure_code == FAILURE_CODE_CONTEXT_RETRIEVAL_FAILED
    assert result.retryable is False
    assert result.fallback_allowed is True
    # The composer MUST NOT have been called.
    assert composer.calls == []


@pytest.mark.anyio
async def test_context_service_retryable_preserved() -> None:
    context_service = _FakeContextService(
        raise_exc=ArticleRagContextServiceError(
            "embedding provider raised RuntimeError",
            retryable=True,
            failure_code=FAILURE_CODE_CONTEXT_RETRIEVAL_FAILED,
        )
    )
    resolver = _build_resolver(context_service=context_service)
    result = await resolver.resolve_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
    )
    assert result.status == "not_indexed_or_unavailable"
    assert result.retryable is True
    assert result.failure_code == FAILURE_CODE_CONTEXT_RETRIEVAL_FAILED


# ---------------------------------------------------------------------------
# 5. Composer typed error → composer_rejected
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_composer_typed_error_composer_rejected() -> None:
    context_service = _FakeContextService(
        pack_factory=lambda **kw: _make_pack()
    )
    composer = _FakeComposer(
        raise_exc=ArticleRagAskContextComposerError(
            "ask context composer item at index=0 has empty text",
            retryable=False,
            failure_code=FAILURE_CODE_ASK_CONTEXT_EMPTY_TEXT,
        )
    )
    resolver = _build_resolver(
        context_service=context_service, composer=composer
    )
    # MUST NOT raise.
    result = await resolver.resolve_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
    )
    assert result.status == "composer_rejected"
    assert result.bundle is None
    assert result.failure_code == FAILURE_CODE_ASK_CONTEXT_EMPTY_TEXT
    assert result.retryable is False
    assert result.fallback_allowed is True


# ---------------------------------------------------------------------------
# 6. Unexpected exception → unexpected_error code, no leak
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_unexpected_context_service_exception_not_indexed() -> None:
    secret = "SECRET-QUERY-DO-NOT-LEAK-DO-NOT-LEAK"
    secret_message = (
        f"context service exploded with internal diagnostics "
        f"involving {secret}"
    )
    context_service = _FakeContextService(
        raise_exc=RuntimeError(secret_message)
    )
    composer = _FakeComposer(bundle_factory=lambda pack: _make_bundle(pack))
    resolver = _build_resolver(
        context_service=context_service, composer=composer
    )
    result = await resolver.resolve_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text=secret,
    )
    assert result.status == "not_indexed_or_unavailable"
    assert result.failure_code == FAILURE_CODE_RESOLVER_UNEXPECTED_ERROR
    assert result.fallback_allowed is True
    # The original message MUST NOT leak anywhere in the result.
    assert secret not in repr(result)
    assert secret not in str(result)
    # The query text MUST NOT leak.
    assert secret_message not in repr(result)


@pytest.mark.anyio
async def test_unexpected_composer_exception_composer_rejected() -> None:
    secret = "SECRET-COMPOSER-DIAGNOSTIC-DO-NOT-LEAK"
    context_service = _FakeContextService(
        pack_factory=lambda **kw: _make_pack()
    )
    composer = _FakeComposer(
        raise_exc=RuntimeError(f"composer exploded: {secret}")
    )
    resolver = _build_resolver(
        context_service=context_service, composer=composer
    )
    result = await resolver.resolve_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
    )
    assert result.status == "composer_rejected"
    assert result.failure_code == FAILURE_CODE_RESOLVER_UNEXPECTED_ERROR
    assert secret not in repr(result)
    assert secret not in str(result)


# ---------------------------------------------------------------------------
# 7. Missing dependency config
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_missing_context_service_fails_soft() -> None:
    resolver = ArticleRagAskContextResolver(
        composer=_FakeComposer(bundle_factory=lambda pack: _make_bundle(pack)),
    )
    result = await resolver.resolve_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
    )
    assert result.status == "not_indexed_or_unavailable"
    assert result.failure_code == FAILURE_CODE_RESOLVER_UNEXPECTED_ERROR
    assert result.fallback_allowed is True


@pytest.mark.anyio
async def test_missing_composer_fails_soft() -> None:
    resolver = ArticleRagAskContextResolver(
        context_service=_FakeContextService(
            pack_factory=lambda **kw: _make_pack()
        ),
    )
    result = await resolver.resolve_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
    )
    assert result.status == "not_indexed_or_unavailable"
    assert result.failure_code == FAILURE_CODE_RESOLVER_UNEXPECTED_ERROR


# ---------------------------------------------------------------------------
# 8. query_sha256
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_query_sha256_deterministic() -> None:
    pack = _make_pack()
    context_service = _FakeContextService(
        pack_factory=lambda **kw: pack
    )
    resolver = ArticleRagAskContextResolver(
        context_service=context_service,
        composer=_FakeComposer(bundle_factory=lambda pack: _make_bundle(pack)),
    )
    secret = "SECRET-QUERY-DO-NOT-LEAK"
    r1 = await resolver.resolve_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text=secret,
    )
    r2 = await resolver.resolve_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text=secret,
    )
    assert r1.query_sha256 == r2.query_sha256
    assert r1.query_sha256 == hashlib.sha256(secret.encode("utf-8")).hexdigest()


@pytest.mark.anyio
async def test_query_text_not_in_repr_or_str() -> None:
    pack = _make_pack()
    context_service = _FakeContextService(
        pack_factory=lambda **kw: pack
    )
    resolver = ArticleRagAskContextResolver(
        context_service=context_service,
        composer=_FakeComposer(bundle_factory=lambda pack: _make_bundle(pack)),
    )
    secret = "SECRET-QUERY-DO-NOT-LEAK-DO-NOT-LEAK"
    result = await resolver.resolve_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text=secret,
    )
    assert secret not in repr(result)
    assert secret not in str(result)


# ---------------------------------------------------------------------------
# 9. provider_metadata NOT in result
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_result_does_not_carry_provider_metadata() -> None:
    pack = _make_pack(
        provider_metadata={
            "provider": "zilliz",
            "latency_ms": 42,
            "secret": "DO-NOT-LEAK",
        }
    )
    context_service = _FakeContextService(
        pack_factory=lambda **kw: pack
    )
    resolver = ArticleRagAskContextResolver(
        context_service=context_service,
        composer=_FakeComposer(bundle_factory=lambda pack: _make_bundle(pack)),
    )
    result = await resolver.resolve_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
    )
    # No provider_metadata anywhere on the result.
    assert not hasattr(result, "provider_metadata")
    repr_text = repr(result)
    assert "zilliz" not in repr_text
    assert "latency_ms" not in repr_text
    assert "DO-NOT-LEAK" not in repr_text


# ---------------------------------------------------------------------------
# 10. fallback_allowed policy
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_fallback_allowed_for_every_non_ok_status() -> None:
    """The Ask layer is allowed to fall back to a no-RAG answer
    for EVERY non-OK status (no hard errors)."""
    # available → fallback_allowed should reflect the contract:
    # even the OK path is "fallback allowed" because the Ask layer
    # can choose to ignore RAG; the field describes whether the
    # Ask layer MAY fall back, not whether it MUST.
    # Per the contract spec, fallback_allowed=True on every path.
    pack = _make_pack()
    context_service = _FakeContextService(
        pack_factory=lambda **kw: pack
    )
    resolver = ArticleRagAskContextResolver(
        context_service=context_service,
        composer=_FakeComposer(bundle_factory=lambda pack: _make_bundle(pack)),
    )
    # disabled
    r = await resolver.resolve_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
        enabled=False,
    )
    assert r.fallback_allowed is True
    # available
    r = await resolver.resolve_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
    )
    assert r.fallback_allowed is True
    # empty
    context_service_empty = _FakeContextService(
        pack_factory=lambda **kw: _make_pack(items=[])
    )
    resolver2 = ArticleRagAskContextResolver(
        context_service=context_service_empty,
        composer=_FakeComposer(bundle_factory=lambda pack: _make_bundle(pack)),
    )
    r = await resolver2.resolve_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
    )
    assert r.fallback_allowed is True
    # not_indexed_or_unavailable
    context_service_err = _FakeContextService(
        raise_exc=ArticleRagContextServiceError(
            "boom",
            retryable=False,
            failure_code=FAILURE_CODE_CONTEXT_RETRIEVAL_FAILED,
        )
    )
    resolver3 = ArticleRagAskContextResolver(
        context_service=context_service_err,
        composer=_FakeComposer(bundle_factory=lambda pack: _make_bundle(pack)),
    )
    r = await resolver3.resolve_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
    )
    assert r.fallback_allowed is True


# ---------------------------------------------------------------------------
# 11. Constants / status literal
# ---------------------------------------------------------------------------


def test_default_constants() -> None:
    assert DEFAULT_RESOLVER_LIMIT == 8
    assert DEFAULT_RESOLVER_MAX_CONTEXT_CHARS == 4000


def test_failure_codes_are_distinct() -> None:
    codes = {
        FAILURE_CODE_RESOLVER_DISABLED,
        FAILURE_CODE_RESOLVER_COMPOSER_REJECTED,
        FAILURE_CODE_RESOLVER_UNEXPECTED_ERROR,
    }
    assert len(codes) == 3


def test_status_literal_values() -> None:
    # Pin the exact Literal values — ops dashboards dispatch on
    # these strings; a typo here would silently break them.
    assert ArticleRagAskContextResolveResult(
        status="available",
        enabled=True,
        bundle=None,
        failure_code=None,
        retryable=False,
        fallback_allowed=True,
        reading_record_id=None,
        query_sha256=None,
    ).status == "available"
    assert ArticleRagAskContextResolveResult(
        status="empty",
        enabled=True,
        bundle=None,
        failure_code=None,
        retryable=False,
        fallback_allowed=True,
        reading_record_id=None,
        query_sha256=None,
    ).status == "empty"
    assert ArticleRagAskContextResolveResult(
        status="disabled",
        enabled=True,
        bundle=None,
        failure_code=None,
        retryable=False,
        fallback_allowed=True,
        reading_record_id=None,
        query_sha256=None,
    ).status == "disabled"
    assert ArticleRagAskContextResolveResult(
        status="composer_rejected",
        enabled=True,
        bundle=None,
        failure_code=None,
        retryable=False,
        fallback_allowed=True,
        reading_record_id=None,
        query_sha256=None,
    ).status == "composer_rejected"
    assert ArticleRagAskContextResolveResult(
        status="not_indexed_or_unavailable",
        enabled=True,
        bundle=None,
        failure_code=None,
        retryable=False,
        fallback_allowed=True,
        reading_record_id=None,
        query_sha256=None,
    ).status == "not_indexed_or_unavailable"


# ---------------------------------------------------------------------------
# 12. Echoed stable ids on empty / error results
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_empty_result_echoes_stable_ids() -> None:
    pack = _make_pack(items=[])
    context_service = _FakeContextService(
        pack_factory=lambda **kw: pack
    )
    resolver = ArticleRagAskContextResolver(
        context_service=context_service,
        composer=_FakeComposer(bundle_factory=lambda p: _make_bundle(p)),
    )
    result = await resolver.resolve_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
    )
    assert result.status == "empty"
    assert result.stable_document_id == _STABLE_DOC_ID
    assert result.base_id == _BASE_ID
    assert result.record_generation == 1
    assert result.plan_content_sha256 == _PLAN_HASH
    assert result.omitted_hit_count == 0
    assert result.budget_exceeded is False


@pytest.mark.anyio
async def test_composer_rejected_echoes_stable_ids() -> None:
    pack = _make_pack()
    context_service = _FakeContextService(
        pack_factory=lambda **kw: pack
    )
    composer = _FakeComposer(
        raise_exc=ArticleRagAskContextComposerError(
            "boom",
            retryable=False,
            failure_code=FAILURE_CODE_ASK_CONTEXT_EMPTY_TEXT,
        )
    )
    resolver = ArticleRagAskContextResolver(
        context_service=context_service, composer=composer
    )
    result = await resolver.resolve_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
    )
    assert result.status == "composer_rejected"
    # The context service was called and produced a pack; the
    # stable ids are available on the result even though the
    # bundle is None — the Ask layer can use them for cache
    # keys / log dedup.
    assert result.stable_document_id == _STABLE_DOC_ID
    assert result.base_id == _BASE_ID
    assert result.plan_content_sha256 == _PLAN_HASH


# ---------------------------------------------------------------------------
# 13. Determinism
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_resolver_deterministic_for_same_input() -> None:
    pack = _make_pack()

    async def _run_once() -> ArticleRagAskContextResolveResult:
        context_service = _FakeContextService(
            pack_factory=lambda **kw: pack
        )
        resolver = ArticleRagAskContextResolver(
            context_service=context_service,
            composer=_FakeComposer(
                bundle_factory=lambda p: _make_bundle(p)
            ),
        )
        return await resolver.resolve_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="hello",
        )

    r1 = await _run_once()
    r2 = await _run_once()
    assert r1.status == r2.status
    assert r1.query_sha256 == r2.query_sha256
    assert r1.failure_code == r2.failure_code
    assert r1.omitted_hit_count == r2.omitted_hit_count
    assert r1.budget_exceeded == r2.budget_exceeded


# ---------------------------------------------------------------------------
# 14. Reviewer fixes: local query_hash + composer defensive check
# ---------------------------------------------------------------------------


def _make_pack_with_mismatched_query_sha256(
    *, items: list[ArticleRagContextItem] | None = None
) -> ArticleRagContextPack:
    """A pack whose ``query_sha256`` deliberately does NOT match
    what the resolver would compute for the same ``query_text``.

    Used to pin the contract: the resolver MUST use its own
    locally computed hash on every path, not whatever the context
    service / pack happens to surface.
    """
    # Use a totally unrelated 64-char string as the pack's
    # query_sha256.
    bogus_pack_hash = "f" * 64
    return ArticleRagContextPack(
        reading_record_id=_RECORD_ID,
        stable_document_id=_STABLE_DOC_ID,
        base_id=_BASE_ID,
        record_generation=1,
        plan_content_sha256=_PLAN_HASH,
        query_sha256=bogus_pack_hash,
        items=tuple(items if items is not None else []),
        total_text_chars=sum(len(it.text) for it in (items or [])),
        omitted_hit_count=0,
        budget_exceeded=False,
        max_context_chars=4000,
        provider_metadata={"provider": "zilliz"},
    )


@pytest.mark.anyio
async def test_empty_branch_uses_local_query_hash_not_pack_hash() -> (
    None
):
    """Reviewer P1 fix: even when the context service returns a
    pack with a mismatched ``query_sha256``, the resolver MUST
    surface the value it computed locally for the
    ``query_text`` it actually received.
    """
    pack = _make_pack_with_mismatched_query_sha256(items=[])
    # The pack has no items so the resolver returns status=empty.
    assert not pack.items
    context_service = _FakeContextService(
        pack_factory=lambda **kw: pack
    )
    resolver = ArticleRagAskContextResolver(
        context_service=context_service,
        composer=_FakeComposer(bundle_factory=lambda p: _make_bundle(p)),
    )
    secret_query = "SECRET-QUERY-DO-NOT-LEAK"
    result = await resolver.resolve_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text=secret_query,
    )
    assert result.status == "empty"
    # The local hash matches the secret query, NOT the bogus
    # pack hash.  Defence in depth: a future regression that
    # returns ``pack.query_sha256`` here would put the bogus
    # ``"f" * 64`` value on the result.
    assert result.query_sha256 == hashlib.sha256(
        secret_query.encode("utf-8")
    ).hexdigest()
    assert result.query_sha256 != pack.query_sha256


@pytest.mark.anyio
async def test_composer_rejected_branch_uses_local_query_hash_not_pack_hash() -> (
    None
):
    """Reviewer P1 fix: same contract on the composer_rejected
    path.  The locally computed hash is what the Ask layer sees.
    """
    pack = _make_pack_with_mismatched_query_sha256(
        items=[_make_item()]
    )
    context_service = _FakeContextService(
        pack_factory=lambda **kw: pack
    )
    composer = _FakeComposer(
        raise_exc=ArticleRagAskContextComposerError(
            "boom",
            retryable=False,
            failure_code=FAILURE_CODE_ASK_CONTEXT_EMPTY_TEXT,
        )
    )
    resolver = ArticleRagAskContextResolver(
        context_service=context_service, composer=composer
    )
    secret_query = "SECRET-QUERY-DO-NOT-LEAK"
    result = await resolver.resolve_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text=secret_query,
    )
    assert result.status == "composer_rejected"
    assert result.query_sha256 == hashlib.sha256(
        secret_query.encode("utf-8")
    ).hexdigest()
    assert result.query_sha256 != pack.query_sha256


@pytest.mark.anyio
async def test_composer_returning_none_maps_to_composer_rejected() -> None:
    """Reviewer P2 fix: a composer regression that returns
    ``None`` instead of a bundle MUST NOT slip through as
    ``status="available"`` with ``bundle=None`` (that would
    violate the result docstring's ``available => bundle
    non-None`` contract).  The resolver must surface this as
    ``status="composer_rejected"`` with a stable failure_code.
    """

    class _NoneReturningComposer:
        def compose(
            self, pack: ArticleRagContextPack
        ) -> "ArticleRagAskContextBundle | None":
            # Regression: returns None instead of a bundle.
            return None

    context_service = _FakeContextService(
        pack_factory=lambda **kw: _make_pack()
    )
    resolver = ArticleRagAskContextResolver(
        context_service=context_service,
        composer=_NoneReturningComposer(),
    )
    result = await resolver.resolve_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
    )
    assert result.status == "composer_rejected"
    assert result.bundle is None
    assert result.failure_code == FAILURE_CODE_RESOLVER_UNEXPECTED_ERROR
    assert result.fallback_allowed is True
    # Stable ids still echoed (the pack is still in hand).
    assert result.stable_document_id == _STABLE_DOC_ID
    assert result.plan_content_sha256 == _PLAN_HASH


@pytest.mark.anyio
async def test_composer_returning_empty_bundle_maps_to_composer_rejected() -> (
    None
):
    """Reviewer P2 fix: a composer regression that returns a
    bundle with ``empty=True`` for a non-empty pack MUST be
    mapped to ``status="composer_rejected"``.

    (The I4G composer does not do this — ``empty=True`` is only
    set when the input pack is empty, and the resolver
    short-circuits before calling the composer on an empty
    pack.  But a future composer regression could set it on
    a non-empty input; the resolver's invariant check catches
    that.)
    """

    class _EmptyBundleComposer:
        def compose(
            self, pack: ArticleRagContextPack
        ) -> ArticleRagAskContextBundle:
            # Construct a bundle with empty=True even though
            # the input pack has items.  The resolver's
            # invariant check MUST catch this.
            return ArticleRagAskContextBundle(
                prompt_context_text="",
                citations=(),
                context_ids=(),
                source_pack_hash="x" * 64,
                omitted_hit_count=0,
                budget_exceeded=False,
                empty=True,
                reading_record_id=pack.reading_record_id,
                stable_document_id=pack.stable_document_id,
                base_id=pack.base_id,
                record_generation=pack.record_generation,
                plan_content_sha256=pack.plan_content_sha256,
            )

    context_service = _FakeContextService(
        pack_factory=lambda **kw: _make_pack()
    )
    resolver = ArticleRagAskContextResolver(
        context_service=context_service,
        composer=_EmptyBundleComposer(),
    )
    result = await resolver.resolve_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
    )
    assert result.status == "composer_rejected"
    assert result.bundle is None
    assert result.failure_code == FAILURE_CODE_RESOLVER_UNEXPECTED_ERROR
    assert result.fallback_allowed is True