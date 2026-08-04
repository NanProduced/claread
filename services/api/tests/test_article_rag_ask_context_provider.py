# task-history: D6-I4N (renamed from test_d6_i4n_article_rag_ask_context_provider.py)
"""D6-I4N: tests for Article RAG ask context provider facade.

Covers:
  * happy path: chain I4J (async) -> I4K -> I4L -> I4M.
  * integration adapter is awaited (real-I4J-style async).
  * no-context paths become no-attach assemblies.
  * any dependency raises -> fail-soft.
  * missing dependency -> fail-soft.
  * repr/str safety.
  * no DB / network / LLM.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

import pytest

from app.services.reader_orchestration.article_rag_ask_context_provider import (
    DEFAULT_FACADE_LIMIT,
    DEFAULT_FACADE_MAX_CONTEXT_CHARS,
    FAILURE_CODE_FACADE_UNEXPECTED_ERROR,
    ArticleRagAskContextProvider,
)
from app.services.reader_orchestration.article_rag_ask_prompt_assembly import (
    ArticleRagAskPromptAssembly,
)
from app.services.reader_orchestration.article_rag_ask_integration_adapter import (
    ArticleRagAskIntegrationAdapter,
    ArticleRagAskPromptSegment,
)
from app.services.reader_orchestration.article_rag_ask_runtime_adapter import (
    ArticleRagAskRuntimeContext,
)

pytestmark = [
    pytest.mark.chain_article_rag,
    pytest.mark.seam_pure_unit,
    pytest.mark.life_permanent_regression,
]


_RECORD_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
_USER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
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


def _make_citation(*, context_id, chunk_id, block="block-x"):
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
    *, include_in_prompt=True, status="available",
    prompt_text=None, citations=None, context_ids=None,
    source_pack_hash=_SOURCE_PACK_HASH, query_sha256=None,
    failure_code=None,
):
    if prompt_text is None:
        prompt_text = _PROMPT_TEXT
    if citations is None:
        citations = (_make_citation(context_id="rag-1", chunk_id="c1"),)
    if context_ids is None:
        context_ids = ("rag-1",)
    if query_sha256 is None:
        query_sha256 = hashlib.sha256(b"hello").hexdigest()
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


def _make_context(
    *, should_attach=True, status="available",
    prompt_section_text=None, citations=None, context_ids=None,
    source_pack_hash=_SOURCE_PACK_HASH, query_sha256=None,
    failure_code=None,
):
    if prompt_section_text is None:
        prompt_section_text = _PROMPT_SECTION_TEXT
    if citations is None:
        citations = (_make_citation(context_id="rag-1", chunk_id="c1"),)
    if context_ids is None:
        context_ids = ("rag-1",)
    if query_sha256 is None:
        query_sha256 = hashlib.sha256(b"hello").hexdigest()
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


class _FakeIntegrationAdapter:
    """Stand-in for ``ArticleRagAskIntegrationAdapter``.

    The REAL I4J integration adapter is async def.  This fake
    is async to mirror the real contract.
    """

    def __init__(self, *, segment):
        self._segment = segment
        self.calls = []

    async def build_prompt_segment(self, **kwargs):
        self.calls.append(kwargs)
        return self._segment


class _FakeSectionBuilder:
    def __init__(self, *, section_text):
        self._section_text = section_text
        self.calls = []

    def build(self, segment):
        self.calls.append(segment)
        return _make_context(
            should_attach=True, status="available",
            prompt_section_text=self._section_text,
        )


class _FakeRuntimeAdapter:
    def __init__(self, *, context):
        self._context = context
        self.calls = []

    def build(self, section):
        self.calls.append(section)
        return self._context


class _FakeAssemblyService:
    def __init__(self, *, assembly):
        self._assembly = assembly
        self.calls = []

    def assemble(self, context):
        self.calls.append(context)
        return self._assembly


def _build_provider(
    *, integration_adapter=None, section_builder=None,
    runtime_adapter=None, assembly_service=None,
):
    return ArticleRagAskContextProvider(
        integration_adapter=integration_adapter,
        section_builder=section_builder,
        runtime_adapter=runtime_adapter,
        assembly_service=assembly_service,
    )


def _build_full_chain(*, segment=None, context=None, assembly=None):
    if segment is None:
        segment = _make_segment()
    if context is None:
        context = _make_context()
    if assembly is None:
        assembly = ArticleRagAskPromptAssembly(
            kind="article_rag_context",
            should_attach=True,
            prompt_attachment_block=_PROMPT_SECTION_TEXT,
            citations=(_make_citation(context_id="rag-1", chunk_id="c1"),),
            context_ids=("rag-1",),
            source_pack_hash=_SOURCE_PACK_HASH,
            query_sha256=hashlib.sha256(b"hello").hexdigest(),
            status="available", failure_code=None,
            retryable=False, fallback_allowed=True,
            metadata_json={"status": "available"},
        )
    return _build_provider(
        integration_adapter=_FakeIntegrationAdapter(segment=segment),
        section_builder=_FakeSectionBuilder(
            section_text=_PROMPT_SECTION_TEXT
        ),
        runtime_adapter=_FakeRuntimeAdapter(context=context),
        assembly_service=_FakeAssemblyService(assembly=assembly),
    )


# 1. Happy path


@pytest.mark.anyio
async def test_happy_path_chains_all_four_layers():
    assembly = ArticleRagAskPromptAssembly(
        kind="article_rag_context",
        should_attach=True,
        prompt_attachment_block=_PROMPT_SECTION_TEXT,
        citations=(_make_citation(context_id="rag-1", chunk_id="c1"),),
        context_ids=("rag-1",),
        source_pack_hash=_SOURCE_PACK_HASH,
        query_sha256=hashlib.sha256(b"hello").hexdigest(),
        status="available", failure_code=None,
        retryable=False, fallback_allowed=True,
        metadata_json={"status": "available"},
    )
    provider = _build_full_chain(assembly=assembly)
    result = await provider.build_for_ask(
        reading_record_id=_RECORD_ID, user_id=_USER_ID,
        query_text="hello",
    )
    assert isinstance(result, ArticleRagAskPromptAssembly)
    assert result.should_attach is True
    assert result.status == "available"
    assert result is assembly


@pytest.mark.anyio
async def test_real_i4j_integration_adapter_awaited_correctly():
    """P1 regression: the facade MUST await the real I4J
    integration adapter.  A synchronous call would produce a
    coroutine object that the downstream layers treat as
    malformed.
    """

    class _AsyncAttachmentService:
        async def build_for_ask(self, **kwargs):
            return _make_segment()

    real_integration_adapter = ArticleRagAskIntegrationAdapter(
        attachment_service=_AsyncAttachmentService(),
    )
    provider = _build_provider(
        integration_adapter=real_integration_adapter,
        section_builder=_FakeSectionBuilder(
            section_text=_PROMPT_SECTION_TEXT
        ),
        runtime_adapter=_FakeRuntimeAdapter(context=_make_context()),
        assembly_service=_FakeAssemblyService(
            assembly=ArticleRagAskPromptAssembly(
                kind="article_rag_context",
                should_attach=True,
                prompt_attachment_block=_PROMPT_SECTION_TEXT,
                citations=(_make_citation(
                    context_id="rag-1", chunk_id="c1"
                ),),
                context_ids=("rag-1",),
                source_pack_hash=_SOURCE_PACK_HASH,
                query_sha256=hashlib.sha256(b"hello").hexdigest(),
                status="available", failure_code=None,
                retryable=False, fallback_allowed=True,
                metadata_json={"status": "available"},
            )
        ),
    )
    result = await provider.build_for_ask(
        reading_record_id=_RECORD_ID, user_id=_USER_ID,
        query_text="hello",
    )
    assert result.should_attach is True
    assert result.status == "available"


# 2. No-context paths


@pytest.mark.anyio
async def test_disabled_segment_becomes_no_attach_assembly():
    segment = _make_segment(
        include_in_prompt=False, status="disabled",
        prompt_text="", citations=(), context_ids=(),
        source_pack_hash=None,
    )
    no_attach_assembly = ArticleRagAskPromptAssembly(
        kind="article_rag_context",
        should_attach=False, prompt_attachment_block="",
        citations=(), context_ids=(),
        source_pack_hash=None, query_sha256=None,
        status="disabled", failure_code=None,
        retryable=False, fallback_allowed=True,
        metadata_json={"status": "disabled"},
    )
    provider = _build_full_chain(
        segment=segment,
        context=_make_context(
            should_attach=False, status="disabled",
            prompt_section_text="", citations=(), context_ids=(),
            source_pack_hash=None,
        ),
        assembly=no_attach_assembly,
    )
    result = await provider.build_for_ask(
        reading_record_id=_RECORD_ID, user_id=_USER_ID,
        query_text="hello",
    )
    assert result.should_attach is False
    assert result.status == "disabled"
    assert result.fallback_allowed is True


# 3. Any dependency raises -> fail-soft


@pytest.mark.anyio
async def test_integration_adapter_raises_fails_soft():
    secret = "SECRET-QUERY-DO-NOT-LEAK-DO-NOT-LEAK"
    secret_message = f"integration adapter leaked {secret}"

    class _RaisingIntegrationAdapter:
        async def build_prompt_segment(self, **kwargs):
            raise RuntimeError(secret_message)

    provider = _build_provider(
        integration_adapter=_RaisingIntegrationAdapter(),
        section_builder=_FakeSectionBuilder(
            section_text=_PROMPT_SECTION_TEXT
        ),
        runtime_adapter=_FakeRuntimeAdapter(context=_make_context()),
        assembly_service=_FakeAssemblyService(
            assembly=ArticleRagAskPromptAssembly(
                kind="article_rag_context",
                should_attach=False, prompt_attachment_block="",
                citations=(), context_ids=(),
                source_pack_hash=None, query_sha256=None,
                status="not_indexed_or_unavailable",
                failure_code="placeholder",
                retryable=False, fallback_allowed=True,
                metadata_json={},
            )
        ),
    )
    result = await provider.build_for_ask(
        reading_record_id=_RECORD_ID, user_id=_USER_ID,
        query_text=secret,
    )
    assert result.failure_code == FAILURE_CODE_FACADE_UNEXPECTED_ERROR
    assert secret_message not in repr(result)
    assert secret not in repr(result)
    assert secret not in str(result)


@pytest.mark.anyio
async def test_section_builder_raises_fails_soft():
    class _RaisingSectionBuilder:
        def build(self, segment):
            raise RuntimeError("section exploded")

    provider = _build_provider(
        integration_adapter=_FakeIntegrationAdapter(segment=_make_segment()),
        section_builder=_RaisingSectionBuilder(),
        runtime_adapter=_FakeRuntimeAdapter(context=_make_context()),
        assembly_service=_FakeAssemblyService(
            assembly=ArticleRagAskPromptAssembly(
                kind="article_rag_context",
                should_attach=False, prompt_attachment_block="",
                citations=(), context_ids=(),
                source_pack_hash=None, query_sha256=None,
                status="not_indexed_or_unavailable",
                failure_code="placeholder",
                retryable=False, fallback_allowed=True,
                metadata_json={},
            )
        ),
    )
    result = await provider.build_for_ask(
        reading_record_id=_RECORD_ID, user_id=_USER_ID,
        query_text="hello",
    )
    assert result.failure_code == FAILURE_CODE_FACADE_UNEXPECTED_ERROR


@pytest.mark.anyio
async def test_runtime_adapter_raises_fails_soft():
    class _RaisingRuntimeAdapter:
        def build(self, section):
            raise RuntimeError("runtime exploded")

    provider = _build_provider(
        integration_adapter=_FakeIntegrationAdapter(segment=_make_segment()),
        section_builder=_FakeSectionBuilder(
            section_text=_PROMPT_SECTION_TEXT
        ),
        runtime_adapter=_RaisingRuntimeAdapter(),
        assembly_service=_FakeAssemblyService(
            assembly=ArticleRagAskPromptAssembly(
                kind="article_rag_context",
                should_attach=False, prompt_attachment_block="",
                citations=(), context_ids=(),
                source_pack_hash=None, query_sha256=None,
                status="not_indexed_or_unavailable",
                failure_code="placeholder",
                retryable=False, fallback_allowed=True,
                metadata_json={},
            )
        ),
    )
    result = await provider.build_for_ask(
        reading_record_id=_RECORD_ID, user_id=_USER_ID,
        query_text="hello",
    )
    assert result.failure_code == FAILURE_CODE_FACADE_UNEXPECTED_ERROR


@pytest.mark.anyio
async def test_assembly_service_raises_fails_soft():
    class _RaisingAssemblyService:
        def assemble(self, context):
            raise RuntimeError("assembly exploded")

    provider = _build_provider(
        integration_adapter=_FakeIntegrationAdapter(segment=_make_segment()),
        section_builder=_FakeSectionBuilder(
            section_text=_PROMPT_SECTION_TEXT
        ),
        runtime_adapter=_FakeRuntimeAdapter(context=_make_context()),
        assembly_service=_RaisingAssemblyService(),
    )
    result = await provider.build_for_ask(
        reading_record_id=_RECORD_ID, user_id=_USER_ID,
        query_text="hello",
    )
    assert result.failure_code == FAILURE_CODE_FACADE_UNEXPECTED_ERROR


# 4. Missing dependency -> fail-soft


@pytest.mark.anyio
async def test_missing_integration_adapter_fails_soft():
    provider = ArticleRagAskContextProvider(
        integration_adapter=None,
        section_builder=_FakeSectionBuilder(
            section_text=_PROMPT_SECTION_TEXT
        ),
        runtime_adapter=_FakeRuntimeAdapter(context=_make_context()),
        assembly_service=_FakeAssemblyService(
            assembly=ArticleRagAskPromptAssembly(
                kind="article_rag_context",
                should_attach=False, prompt_attachment_block="",
                citations=(), context_ids=(),
                source_pack_hash=None, query_sha256=None,
                status="not_indexed_or_unavailable",
                failure_code="placeholder",
                retryable=False, fallback_allowed=True,
                metadata_json={},
            )
        ),
    )
    result = await provider.build_for_ask(
        reading_record_id=_RECORD_ID, user_id=_USER_ID,
        query_text="hello",
    )
    assert result.failure_code == FAILURE_CODE_FACADE_UNEXPECTED_ERROR
    assert result.should_attach is False


@pytest.mark.anyio
async def test_assembly_service_falls_back_to_default():
    """The facade instantiates a default
    ``ArticleRagAskPromptAssemblyService`` when omitted.
    """
    provider = ArticleRagAskContextProvider(
        integration_adapter=_FakeIntegrationAdapter(segment=_make_segment()),
        section_builder=_FakeSectionBuilder(
            section_text=_PROMPT_SECTION_TEXT
        ),
        runtime_adapter=_FakeRuntimeAdapter(context=_make_context()),
        assembly_service=None,
    )
    result = await provider.build_for_ask(
        reading_record_id=_RECORD_ID, user_id=_USER_ID,
        query_text="hello",
    )
    assert isinstance(result, ArticleRagAskPromptAssembly)


# 5. Malformed intermediate


@pytest.mark.anyio
async def test_malformed_segment_fails_soft():
    class _MalformedAdapter:
        async def build_prompt_segment(self, **kwargs):
            return {"not": "a real ArticleRagAskPromptSegment"}

    provider = _build_provider(
        integration_adapter=_MalformedAdapter(),
        section_builder=_FakeSectionBuilder(
            section_text=_PROMPT_SECTION_TEXT
        ),
        runtime_adapter=_FakeRuntimeAdapter(context=_make_context()),
        assembly_service=_FakeAssemblyService(
            assembly=ArticleRagAskPromptAssembly(
                kind="article_rag_context",
                should_attach=False, prompt_attachment_block="",
                citations=(), context_ids=(),
                source_pack_hash=None, query_sha256=None,
                status="not_indexed_or_unavailable",
                failure_code="placeholder",
                retryable=False, fallback_allowed=True,
                metadata_json={},
            )
        ),
    )
    result = await provider.build_for_ask(
        reading_record_id=_RECORD_ID, user_id=_USER_ID,
        query_text="hello",
    )
    assert isinstance(result, ArticleRagAskPromptAssembly)


# 6. Parameter passthrough


@pytest.mark.anyio
async def test_parameters_passthrough_to_integration_adapter():
    integration_adapter = _FakeIntegrationAdapter(segment=_make_segment())
    provider = _build_provider(
        integration_adapter=integration_adapter,
        section_builder=_FakeSectionBuilder(
            section_text=_PROMPT_SECTION_TEXT
        ),
        runtime_adapter=_FakeRuntimeAdapter(context=_make_context()),
        assembly_service=_FakeAssemblyService(
            assembly=ArticleRagAskPromptAssembly(
                kind="article_rag_context",
                should_attach=False, prompt_attachment_block="",
                citations=(), context_ids=(),
                source_pack_hash=None, query_sha256=None,
                status="not_indexed_or_unavailable",
                failure_code="placeholder",
                retryable=False, fallback_allowed=True,
                metadata_json={},
            )
        ),
    )
    await provider.build_for_ask(
        reading_record_id=_RECORD_ID, user_id=_USER_ID,
        query_text="hello", enabled=False, limit=12,
        max_context_chars=8000,
    )
    assert integration_adapter.calls[0]["enabled"] is False
    assert integration_adapter.calls[0]["limit"] == 12
    assert integration_adapter.calls[0]["max_context_chars"] == 8000


# 7. Default construction


def test_default_constants():
    assert DEFAULT_FACADE_LIMIT == 8
    assert DEFAULT_FACADE_MAX_CONTEXT_CHARS == 4000


def test_failure_code_constant():
    assert (
        FAILURE_CODE_FACADE_UNEXPECTED_ERROR
        == "article_rag_context_provider_unexpected_error"
    )


# 8. No-context assembly passes through


@pytest.mark.anyio
async def test_no_context_assembly_passes_through_chain():
    no_attach_segment = _make_segment(
        include_in_prompt=False, status="empty",
        prompt_text="", citations=(), context_ids=(),
        source_pack_hash=None,
    )
    no_attach_context = _make_context(
        should_attach=False, status="empty",
        prompt_section_text="", citations=(), context_ids=(),
        source_pack_hash=None,
    )
    no_attach_assembly = ArticleRagAskPromptAssembly(
        kind="article_rag_context",
        should_attach=False, prompt_attachment_block="",
        citations=(), context_ids=(),
        source_pack_hash=None, query_sha256=None,
        status="empty", failure_code=None,
        retryable=False, fallback_allowed=True,
        metadata_json={"status": "empty"},
    )
    provider = _build_full_chain(
        segment=no_attach_segment,
        context=no_attach_context,
        assembly=no_attach_assembly,
    )
    result = await provider.build_for_ask(
        reading_record_id=_RECORD_ID, user_id=_USER_ID,
        query_text="hello",
    )
    assert result.should_attach is False
    assert result.status == "empty"
    assert result.prompt_attachment_block == ""
    assert result.citations == ()
    assert result.context_ids == ()


# 9. Determinism


@pytest.mark.anyio
async def test_facade_deterministic_for_same_input():
    provider = _build_full_chain()
    r1 = await provider.build_for_ask(
        reading_record_id=_RECORD_ID, user_id=_USER_ID,
        query_text="hello",
    )
    r2 = await provider.build_for_ask(
        reading_record_id=_RECORD_ID, user_id=_USER_ID,
        query_text="hello",
    )
    assert r1.should_attach == r2.should_attach
    assert r1.status == r2.status
    assert r1.failure_code == r2.failure_code
    assert r1.prompt_attachment_block == r2.prompt_attachment_block
    assert r1.citations == r2.citations
    assert r1.context_ids == r2.context_ids
    assert r1.source_pack_hash == r2.source_pack_hash
    assert r1.query_sha256 == r2.query_sha256
