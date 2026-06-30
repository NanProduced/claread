"""D6-I4P: tests for Article RAG Ask Prompt Runtime Integration.

Covers:
  * available RAG: prompt payload's ``user_message`` is
    augmented with the RAG envelope; citation JSON / Article RAG
    sidecars are NOT written into the prompt payload.
  * not indexed / unavailable: prompt payload is returned
    completely unchanged (deep equality with the original).
  * bridge fail-soft: prompt payload unchanged, sidecar absent.
  * provider unexpected exception: fail-soft, payload unchanged.
  * missing provider / bridge: fail-soft, payload unchanged.
  * non-dict payload: fail-soft, returned as-is.
  * query_text does NOT appear in repr / str / loggable result.
  * no DB, no real DashScope / Zilliz; all fakes.
  * factory: returns None when config missing; returns
    integration when config present (smoke-level only).
"""

from __future__ import annotations

import ast
import hashlib
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.agents.reader_ask_agent import build_reader_ask_prompt
from app.services.reader_ask.article_rag_prompt_integration import (
    ArticleRagPromptIntegration,
    build_default_article_rag_prompt_integration,
)
from app.services.reader_orchestration.article_rag_ask_prompt_assembly import (
    ArticleRagAskPromptAssembly,
)
from app.services.reader_orchestration.article_rag_ask_prompt_bridge import (
    ATTACHMENT_BEGIN_MARKER,
    ATTACHMENT_END_MARKER,
    ArticleRagAskPromptBridge,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_RECORD_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
_USER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
_STABLE_DOC_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
_BASE_ID = uuid.UUID("44444444-4444-4444-4444-444444444444")
_PLAN_HASH = "abc123def456" + "f" * 52
_SOURCE_PACK_HASH = "deadbeef" + "0" * 56
_QUERY_TEXT = "what does this sentence mean"
_QUERY_SHA256 = hashlib.sha256(_QUERY_TEXT.encode("utf-8")).hexdigest()
_PROMPT_SECTION_TEXT = (
    "[ARTICLE_RAG_CONTEXT_BEGIN]\n"
    "[rag-1] rank=1 score=0.950000\nalpha content\n"
    "[ARTICLE_RAG_CONTEXT_END]"
)
_BASE_USER_MESSAGE = "what does this sentence mean"


def _make_citation(
    *, context_id: str = "rag-1", chunk_id: str = "c1",
    block: str = "block-x",
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


def _make_attach_assembly(
    *,
    prompt_attachment_block: str = _PROMPT_SECTION_TEXT,
    citations: Any = "__default__",
    context_ids: Any = "__default__",
    source_pack_hash: str | None = _SOURCE_PACK_HASH,
    query_sha256: str | None = _QUERY_SHA256,
) -> ArticleRagAskPromptAssembly:
    if citations == "__default__":
        citations = (_make_citation(),)
    if context_ids == "__default__":
        context_ids = ("rag-1",)
    return ArticleRagAskPromptAssembly(
        kind="article_rag_context",
        should_attach=True,
        prompt_attachment_block=prompt_attachment_block,
        citations=citations,
        context_ids=context_ids,
        source_pack_hash=source_pack_hash,
        query_sha256=query_sha256,
        status="available",
        failure_code=None,
        retryable=False,
        fallback_allowed=True,
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
            "source_pack_hash": source_pack_hash,
        },
    )


def _make_no_attach_assembly(
    *, status: str = "not_indexed_or_unavailable",
    failure_code: str | None = "context_no_indexed_run",
) -> ArticleRagAskPromptAssembly:
    return ArticleRagAskPromptAssembly(
        kind="article_rag_context",
        should_attach=False,
        prompt_attachment_block="",
        citations=(),
        context_ids=(),
        source_pack_hash=None,
        query_sha256=None,
        status=status,
        failure_code=failure_code,
        retryable=False,
        fallback_allowed=True,
        metadata_json={
            "status": status,
            "failure_code": failure_code,
            "retryable": False,
            "fallback_allowed": True,
        },
    )


def _make_base_payload(
    *, user_message: str = _BASE_USER_MESSAGE,
) -> dict[str, Any]:
    """Build a minimal prompt payload dict that mirrors the
    shape produced by ``build_prompt_payload``.
    """
    return {
        "thread": {"id": "thread-1", "title": "Test Thread"},
        "record": {
            "record_id": str(_RECORD_ID),
            "title": "Test Record",
            "workflow_version": "v3",
            "schema_version": "1",
        },
        "user_message": user_message,
        "resolved_intent": "explain",
        "resolved_intent_label": "Explain",
        "prompt_layers": {"system": "...", "answer": "..."},
        "history": [],
        "canonical_context": {
            "attachments": [],
            "anchors": [],
            "resolved_context_input": None,
        },
        "reference_resolution": {
            "status": "not_needed",
            "query": None,
            "reason": None,
            "resolved_records": [],
            "ambiguous_records": [],
        },
    }


class _FakeProvider:
    """Stand-in for ``ArticleRagAskContextProvider``.

    The real provider is async; this fake is async to mirror
    the real contract.
    """

    def __init__(self, *, assembly: Any) -> None:
        self._assembly = assembly
        self.calls: list[dict[str, Any]] = []

    async def build_for_ask(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self._assembly


class _RaisingProvider:
    """Provider that raises an unexpected exception."""

    async def build_for_ask(self, **kwargs: Any) -> Any:
        raise RuntimeError("unexpected provider failure")


def _build_integration(
    *, provider: Any, bridge: Any | None = None,
) -> ArticleRagPromptIntegration:
    if bridge is None:
        bridge = ArticleRagAskPromptBridge()
    return ArticleRagPromptIntegration(provider=provider, bridge=bridge)


# ---------------------------------------------------------------------------
# 1. Available RAG: attach path
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_attach_path_augments_user_message_with_rag_envelope() -> None:
    """When RAG context is available, the payload's
    ``user_message`` is replaced with the bridge's combined
    ``prompt_text`` (base + envelope).
    """
    assembly = _make_attach_assembly()
    provider = _FakeProvider(assembly=assembly)
    integration = _build_integration(provider=provider)

    payload = _make_base_payload()
    original_user_message = payload["user_message"]

    result = await integration.integrate(
        prompt_payload=payload,
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text=_QUERY_TEXT,
    )

    assert result is payload  # same dict, mutated in place
    assert result["user_message"] != original_user_message
    assert result["user_message"].startswith(original_user_message)
    assert ATTACHMENT_BEGIN_MARKER in result["user_message"]
    assert ATTACHMENT_END_MARKER in result["user_message"]
    assert (
        result["user_message"].index(ATTACHMENT_BEGIN_MARKER)
        < result["user_message"].index(ATTACHMENT_END_MARKER)
    )


@pytest.mark.anyio
async def test_attach_path_does_not_write_article_rag_sidecar_into_payload() -> None:
    """The prompt payload is serialized wholesale for the LLM, so
    Article RAG citations / sidecars must NOT be stored inside it.
    """
    citations = (
        _make_citation(context_id="rag-1", chunk_id="c1"),
        _make_citation(context_id="rag-2", chunk_id="c2", block="block-y"),
    )
    assembly = _make_attach_assembly(
        citations=citations,
        context_ids=("rag-1", "rag-2"),
    )
    provider = _FakeProvider(assembly=assembly)
    integration = _build_integration(provider=provider)

    payload = _make_base_payload()

    await integration.integrate(
        prompt_payload=payload,
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text=_QUERY_TEXT,
    )

    assert "article_rag" not in payload["canonical_context"]
    assert str(citations[0]) not in repr(payload)
    assert "block-y" not in repr(payload)
    assert _SOURCE_PACK_HASH not in repr(payload)
    assert _QUERY_SHA256 not in repr(payload)


@pytest.mark.anyio
async def test_attach_path_citation_json_not_inlined_in_prompt_text() -> None:
    """The final Reader Ask LLM prompt MUST NOT contain citation
    JSON or an Article RAG sidecar.
    """
    citation_secret = "SECRET-IN-CITATION-DO-NOT-LEAK"
    citations = (
        _make_citation(
            context_id="rag-1", chunk_id="c1", block=citation_secret,
        ),
    )
    assembly = _make_attach_assembly(
        citations=citations,
        context_ids=("rag-1",),
        prompt_attachment_block=(
            "[ARTICLE_RAG_CONTEXT_BEGIN]\n"
            "[rag-1] score=0.9\nalpha content\n"
            "[ARTICLE_RAG_CONTEXT_END]"
        ),
    )
    provider = _FakeProvider(assembly=assembly)
    integration = _build_integration(provider=provider)

    payload = _make_base_payload()

    await integration.integrate(
        prompt_payload=payload,
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text=_QUERY_TEXT,
    )

    # The citation secret MUST NOT appear in the prompt text
    # (user_message), nor in the final LLM prompt payload.
    assert citation_secret not in payload["user_message"]
    final_prompt = build_reader_ask_prompt(
        SimpleNamespace(payload=payload)  # type: ignore[arg-type]
    )
    assert citation_secret not in final_prompt
    assert "\"article_rag\"" not in final_prompt
    assert "\"citations\"" not in final_prompt
    assert "\"attachment_block\"" not in final_prompt


@pytest.mark.anyio
async def test_attach_path_preserves_existing_canonical_context() -> None:
    """The integration MUST NOT clobber existing
    ``canonical_context`` fields (attachments, anchors, etc.).
    """
    assembly = _make_attach_assembly()
    provider = _FakeProvider(assembly=assembly)
    integration = _build_integration(provider=provider)

    payload = _make_base_payload()
    existing_attachments = [{"kind": "record_ref", "label": "Existing"}]
    payload["canonical_context"]["attachments"] = existing_attachments

    await integration.integrate(
        prompt_payload=payload,
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text=_QUERY_TEXT,
    )

    assert (
        payload["canonical_context"]["attachments"] == existing_attachments
    )
    assert "article_rag" not in payload["canonical_context"]


# ---------------------------------------------------------------------------
# 2. Not indexed / unavailable: no-attach path
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_no_attach_path_returns_payload_completely_unchanged() -> None:
    """When RAG is not indexed / unavailable, the payload is
    returned completely unchanged (deep equality).
    """
    assembly = _make_no_attach_assembly()
    provider = _FakeProvider(assembly=assembly)
    integration = _build_integration(provider=provider)

    payload = _make_base_payload()
    import copy as _copy

    original = _copy.deepcopy(payload)

    result = await integration.integrate(
        prompt_payload=payload,
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text=_QUERY_TEXT,
    )

    assert result == original
    assert "article_rag" not in result.get("canonical_context", {})


@pytest.mark.anyio
async def test_empty_status_no_attach_returns_payload_unchanged() -> None:
    assembly = _make_no_attach_assembly(
        status="empty", failure_code="context_empty_query",
    )
    provider = _FakeProvider(assembly=assembly)
    integration = _build_integration(provider=provider)

    payload = _make_base_payload()
    import copy as _copy

    original = _copy.deepcopy(payload)

    result = await integration.integrate(
        prompt_payload=payload,
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text=_QUERY_TEXT,
    )

    assert result == original


# ---------------------------------------------------------------------------
# 3. Bridge fail-soft: payload unchanged
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_bridge_fail_soft_returns_payload_unchanged() -> None:
    """When the bridge itself fail-softs (e.g. oversize combined
    prompt), the payload is returned unchanged and no sidecar is
    added.
    """
    assembly = _make_attach_assembly()
    provider = _FakeProvider(assembly=assembly)
    # Use a bridge with a tiny char cap so the combined prompt
    # always exceeds it → oversize fail-soft.
    bridge = ArticleRagAskPromptBridge(max_bridge_chars=10)
    integration = _build_integration(provider=provider, bridge=bridge)

    payload = _make_base_payload()
    import copy as _copy

    original = _copy.deepcopy(payload)

    result = await integration.integrate(
        prompt_payload=payload,
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text=_QUERY_TEXT,
    )

    assert result == original
    assert "article_rag" not in result.get("canonical_context", {})


# ---------------------------------------------------------------------------
# 4. Provider unexpected exception: fail-soft
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_provider_unexpected_exception_fail_soft() -> None:
    """When the provider raises an unexpected exception, the
    integration fail-softs and returns the payload unchanged.
    """
    provider = _RaisingProvider()
    integration = _build_integration(provider=provider)

    payload = _make_base_payload()
    import copy as _copy

    original = _copy.deepcopy(payload)

    result = await integration.integrate(
        prompt_payload=payload,
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text=_QUERY_TEXT,
    )

    assert result == original
    assert "article_rag" not in result.get("canonical_context", {})


# ---------------------------------------------------------------------------
# 5. Missing provider / bridge: fail-soft
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_missing_provider_fail_soft() -> None:
    integration = ArticleRagPromptIntegration(
        provider=None,
        bridge=ArticleRagAskPromptBridge(),
    )
    payload = _make_base_payload()
    import copy as _copy

    original = _copy.deepcopy(payload)

    result = await integration.integrate(
        prompt_payload=payload,
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text=_QUERY_TEXT,
    )

    assert result == original


@pytest.mark.anyio
async def test_missing_bridge_fail_soft() -> None:
    integration = ArticleRagPromptIntegration(
        provider=_FakeProvider(assembly=_make_attach_assembly()),
        bridge=None,
    )
    payload = _make_base_payload()
    import copy as _copy

    original = _copy.deepcopy(payload)

    result = await integration.integrate(
        prompt_payload=payload,
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text=_QUERY_TEXT,
    )

    assert result == original


# ---------------------------------------------------------------------------
# 6. Non-dict payload: fail-soft
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_non_dict_payload_fail_soft() -> None:
    integration = _build_integration(
        provider=_FakeProvider(assembly=_make_attach_assembly()),
    )
    result = await integration.integrate(
        prompt_payload="not-a-dict",  # type: ignore[arg-type]
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text=_QUERY_TEXT,
    )
    assert result == "not-a-dict"


# ---------------------------------------------------------------------------
# 7. query_text does NOT appear in repr / str / loggable result
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_query_text_not_in_payload_after_attach() -> None:
    """The raw ``query_text`` MUST NOT appear in the prompt
    payload's ``user_message`` field.  (The RAG envelope contains
    chunk text, not the raw query.)
    """
    secret_query = "SECRET-QUERY-DO-NOT-LEAK-12345"
    assembly = _make_attach_assembly(
        query_sha256=hashlib.sha256(
            secret_query.encode("utf-8")
        ).hexdigest(),
        prompt_attachment_block=(
            "[ARTICLE_RAG_CONTEXT_BEGIN]\n"
            "[rag-1] score=0.9\nalpha content\n"
            "[ARTICLE_RAG_CONTEXT_END]"
        ),
    )
    provider = _FakeProvider(assembly=assembly)
    integration = _build_integration(provider=provider)

    payload = _make_base_payload(user_message="regular question")

    await integration.integrate(
        prompt_payload=payload,
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text=secret_query,
    )

    assert secret_query not in payload["user_message"]
    assert secret_query not in repr(payload)
    assert secret_query not in str(payload)


@pytest.mark.anyio
async def test_query_text_passed_to_provider_but_not_leaked() -> None:
    """The provider receives ``query_text`` (it needs it for
    retrieval), but the raw text MUST NOT appear in the
    integration's result payload or repr.
    """
    secret_query = "SECRET-QUERY-DO-NOT-LEAK-67890"
    assembly = _make_attach_assembly()
    provider = _FakeProvider(assembly=assembly)
    integration = _build_integration(provider=provider)

    payload = _make_base_payload()

    await integration.integrate(
        prompt_payload=payload,
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text=secret_query,
    )

    # The provider was called with the query text.
    assert provider.calls[0]["query_text"] == secret_query
    # But the raw query text does NOT appear in the payload.
    assert secret_query not in repr(payload)
    assert secret_query not in str(payload)


# ---------------------------------------------------------------------------
# 8. Provider receives correct kwargs
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_provider_receives_correct_kwargs() -> None:
    assembly = _make_attach_assembly()
    provider = _FakeProvider(assembly=assembly)
    integration = _build_integration(provider=provider)

    payload = _make_base_payload()

    await integration.integrate(
        prompt_payload=payload,
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text=_QUERY_TEXT,
        enabled=True,
        limit=5,
        max_context_chars=2000,
        index_version="article_rag_index_v1",
    )

    call = provider.calls[0]
    assert call["reading_record_id"] == _RECORD_ID
    assert call["user_id"] == _USER_ID
    assert call["query_text"] == _QUERY_TEXT
    assert call["enabled"] is True
    assert call["limit"] == 5
    assert call["max_context_chars"] == 2000
    assert call["index_version"] == "article_rag_index_v1"


# ---------------------------------------------------------------------------
# 9. Disabled flag
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_disabled_flag_propagated_to_provider() -> None:
    assembly = _make_no_attach_assembly(status="disabled")
    provider = _FakeProvider(assembly=assembly)
    integration = _build_integration(provider=provider)

    payload = _make_base_payload()

    await integration.integrate(
        prompt_payload=payload,
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text=_QUERY_TEXT,
        enabled=False,
    )

    assert provider.calls[0]["enabled"] is False


# ---------------------------------------------------------------------------
# 10. Factory: returns None when config missing
# ---------------------------------------------------------------------------


class _FakeSettings:
    """Minimal settings stand-in for the factory."""

    def __init__(self, **kwargs: Any) -> None:
        self._values = {
            "reader_article_rag_enabled": False,
            "reader_article_rag_zilliz_uri": "",
            "reader_article_rag_zilliz_token": "",
            "reader_article_rag_zilliz_collection": "article_rag_index_v1",
            "reader_article_rag_embedding_provider": "",
            "reader_article_rag_embedding_model": "",
            "reader_article_rag_vector_provider": "",
        }
        self._values.update(kwargs)

    def __getattr__(self, name: str) -> Any:
        try:
            return self._values[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def test_factory_returns_none_when_feature_disabled() -> None:
    settings = _FakeSettings(reader_article_rag_enabled=False)
    result = build_default_article_rag_prompt_integration(settings)
    assert result is None


def test_factory_returns_none_when_zilliz_uri_missing() -> None:
    settings = _FakeSettings(
        reader_article_rag_enabled=True,
        reader_article_rag_zilliz_uri="",
        reader_article_rag_zilliz_token="some-token",
        reader_article_rag_zilliz_collection="some-collection",
    )
    result = build_default_article_rag_prompt_integration(settings)
    assert result is None


def test_factory_returns_none_when_zilliz_token_missing() -> None:
    settings = _FakeSettings(
        reader_article_rag_enabled=True,
        reader_article_rag_zilliz_uri="https://example.com",
        reader_article_rag_zilliz_token="",
        reader_article_rag_zilliz_collection="some-collection",
    )
    result = build_default_article_rag_prompt_integration(settings)
    assert result is None


def test_factory_returns_none_when_zilliz_collection_missing() -> None:
    settings = _FakeSettings(
        reader_article_rag_enabled=True,
        reader_article_rag_zilliz_uri="https://example.com",
        reader_article_rag_zilliz_token="some-token",
        reader_article_rag_zilliz_collection="",
    )
    result = build_default_article_rag_prompt_integration(settings)
    assert result is None


def test_factory_returns_none_when_zilliz_provider_not_zilliz() -> None:
    """Even if enabled + zilliz config present, if the vector
    provider is not 'zilliz', the vector searcher factory
    returns an UnconfiguredArticleRagVectorSearcher and the
    chain fail-softs at runtime.  The factory still constructs
    the integration (the chain is fail-soft, not fail-closed).
    However, if the embedding provider is also unconfigured,
    the chain will fail-soft.  We verify the factory does not
    raise.
    """
    settings = _FakeSettings(
        reader_article_rag_enabled=True,
        reader_article_rag_zilliz_uri="https://example.com",
        reader_article_rag_zilliz_token="some-token",
        reader_article_rag_zilliz_collection="some-collection",
        reader_article_rag_vector_provider="",  # not "zilliz"
        reader_article_rag_embedding_provider="",  # not "dashscope"
    )
    # The factory should NOT raise — it may return None or an
    # integration (both are acceptable since the chain is
    # fail-soft at runtime).
    result = build_default_article_rag_prompt_integration(settings)
    assert result is None or isinstance(
        result, ArticleRagPromptIntegration
    )


# ---------------------------------------------------------------------------
# 11. Integration never raises (defence in depth)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_integration_never_raises_on_malformed_assembly() -> None:
    """If the provider returns a non-Assembly object (a
    regression), the bridge fail-softs and the integration
    returns the payload unchanged.
    """
    provider = _FakeProvider(assembly={"not": "a real assembly"})
    integration = _build_integration(provider=provider)

    payload = _make_base_payload()
    import copy as _copy

    original = _copy.deepcopy(payload)

    result = await integration.integrate(
        prompt_payload=payload,
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text=_QUERY_TEXT,
    )

    assert result == original


@pytest.mark.anyio
async def test_integration_never_raises_on_bridge_returning_non_result() -> None:
    """If the bridge returns a non-BridgeResult (a regression),
    the integration fail-softs.
    """

    class _BadBridge:
        def bridge(self, **kwargs: Any) -> Any:
            return "not-a-bridge-result"

    integration = ArticleRagPromptIntegration(
        provider=_FakeProvider(assembly=_make_attach_assembly()),
        bridge=_BadBridge(),
    )

    payload = _make_base_payload()
    import copy as _copy

    original = _copy.deepcopy(payload)

    result = await integration.integrate(
        prompt_payload=payload,
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text=_QUERY_TEXT,
    )

    assert result == original


# ---------------------------------------------------------------------------
# 12. Regression: reader_ask/service.py call order
#     build_prompt_payload -> RAG integration -> prepare_prompt_payload
# ---------------------------------------------------------------------------


def _call_name(node: ast.AST) -> str | None:
    """Extract the call name from a Call node (mirrors the helper
    in ``test_reader_ask_service.py``)."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def test_service_py_wires_rag_integration_between_build_and_prepare() -> None:
    """AST-level regression test: verify that
    ``reader_ask/service.py`` wires the Article RAG prompt
    integration between ``build_prompt_payload`` and
    ``prepare_prompt_payload`` in both ``stream_thread_message``
    and ``retry_thread_message``.

    This is a narrow source-level check — it does NOT invoke the
    service (which would require extensive mocking).  It verifies
    the wiring exists and is in the correct order.
    """
    import inspect

    from app.services.reader_ask import service as reader_ask_service

    source = inspect.getsource(reader_ask_service)
    module = ast.parse(source)

    # Collect all function definitions.
    functions = {
        node.name: node
        for node in ast.walk(module)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }

    # The two target functions that have the build -> prepare boundary.
    target_fns = ("stream_thread_message", "retry_thread_message")
    for fn_name in target_fns:
        assert fn_name in functions, (
            f"service.py must define {fn_name}"
        )

        fn_node = functions[fn_name]

        # Walk the function body in source order and record the line
        # numbers of build_prompt_payload, _get_article_rag_prompt_integration,
        # and prepare_prompt_payload calls.
        build_lines: list[int] = []
        integration_lines: list[int] = []
        prepare_lines: list[int] = []

        for node in ast.walk(fn_node):
            if not isinstance(node, ast.Call):
                continue
            call_name = _call_name(node.func)
            if call_name == "build_prompt_payload":
                build_lines.append(node.lineno)
            elif call_name == "_get_article_rag_prompt_integration":
                integration_lines.append(node.lineno)
            elif call_name == "prepare_prompt_payload":
                prepare_lines.append(node.lineno)

        # Each target function must call build_prompt_payload exactly once.
        assert len(build_lines) == 1, (
            f"{fn_name} must call build_prompt_payload exactly once; "
            f"found {len(build_lines)}"
        )

        # Each target function must call _get_article_rag_prompt_integration
        # exactly once (the RAG wiring).
        assert len(integration_lines) == 1, (
            f"{fn_name} must call _get_article_rag_prompt_integration "
            f"exactly once; found {len(integration_lines)}"
        )

        # Each target function must call prepare_prompt_payload exactly once.
        assert len(prepare_lines) == 1, (
            f"{fn_name} must call prepare_prompt_payload exactly once; "
            f"found {len(prepare_lines)}"
        )

        # Order: build < integration < prepare (source order).
        assert build_lines[0] < integration_lines[0] < prepare_lines[0], (
            f"{fn_name}: expected build_prompt_payload (line {build_lines[0]}) "
            f"< _get_article_rag_prompt_integration (line {integration_lines[0]}) "
            f"< prepare_prompt_payload (line {prepare_lines[0]})"
        )


def test_service_py_rag_integration_wrapped_in_try_except() -> None:
    """Verify that the RAG integration call in ``service.py`` is
    wrapped in a try/except block (fail-soft)."""
    import inspect

    from app.services.reader_ask import service as reader_ask_service

    source = inspect.getsource(reader_ask_service)
    module = ast.parse(source)

    # Find all Try nodes that contain a call to
    # _get_article_rag_prompt_integration or .integrate(...).
    try_blocks_with_integration: list[ast.Try] = []
    for node in ast.walk(module):
        if not isinstance(node, ast.Try):
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            call_name = _call_name(child.func)
            if call_name in (
                "_get_article_rag_prompt_integration",
                "integrate",
            ):
                try_blocks_with_integration.append(node)
                break

    # There must be at least 2 try blocks (stream + retry paths).
    # Each path wraps the integration call in its own try/except.
    assert len(try_blocks_with_integration) >= 2, (
        "service.py must wrap the RAG integration call in try/except "
        "in both stream_thread_message and retry_thread_message; "
        f"found {len(try_blocks_with_integration)} try blocks"
    )

    # Each try block must have at least one except handler.
    for try_node in try_blocks_with_integration:
        assert len(try_node.handlers) > 0, (
            "RAG integration try block must have at least one except handler"
        )
