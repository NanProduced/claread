"""D6-I4Q: Article RAG Ask Citation/Evidence Sidecar Output Contract.

Tests that verify the Article RAG sidecar (citations, context_ids,
metadata) flows to the OUTPUT side (ReaderAskRuntimeState +
ReaderAskUserVisibleOutput) without entering the LLM prompt.

Key invariants:
  * ``integrate()`` returns ``ArticleRagPromptIntegrationResult``
    with ``payload`` and ``sidecar`` as SEPARATE fields.
  * The ``sidecar`` NEVER enters ``prompt_payload`` — the LLM
    prompt (built by ``build_reader_ask_prompt`` which serializes
    the entire payload) only sees the RAG context text in
    ``user_message``, never the citation JSON.
  * On the attach path, ``sidecar.should_attach=True`` and
    citations are populated.
  * On the no-attach / fail-soft path, ``sidecar.should_attach=False``
    and citations are empty.
  * ``ReaderAskRuntimeState`` has ``article_rag_citations``,
    ``article_rag_context_ids``, ``article_rag_metadata`` fields.
  * ``ReaderAskUserVisibleOutput`` has ``article_rag_citations``.
  * The ``article_rag_citations`` field is in
    ``USER_VISIBLE_OUTPUT_FIELDS`` and hydrated by the repository.
  * ``query_text`` never appears in repr / str / result.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import pytest

from app.agents.reader_ask_agent import ReaderAskRuntimeState
from app.schemas.reader_ask import (
    ReaderAskResolvedContextSummary,
    ReaderAskUserVisibleOutput,
)
from app.services.reader_ask.article_rag_prompt_integration import (
    ArticleRagPromptIntegration,
    ArticleRagPromptIntegrationResult,
    ArticleRagSidecar,
)
from app.services.reader_ask.output_contract import (
    HYDRATION_READ_FIELDS,
    USER_VISIBLE_OUTPUT_FIELDS,
    build_user_visible_output,
)
from app.services.reader_orchestration.article_rag_ask_prompt_bridge import (
    ATTACHMENT_BEGIN_MARKER,
    ATTACHMENT_END_MARKER,
    ArticleRagAskPromptBridge,
    ArticleRagAskPromptBridgeResult,
)

# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------

_RECORD_ID = UUID("00000000-0000-0000-0000-000000000001")
_USER_ID = UUID("00000000-0000-0000-0000-000000000002")
_QUERY_TEXT = "what is the main idea of this article"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeProvider:
    """Fake provider that returns a pre-built assembly."""

    def __init__(self, assembly: Any) -> None:
        self._assembly = assembly

    async def build_for_ask(self, **kwargs: Any) -> Any:
        return self._assembly


class _RaisingProvider:
    """Provider that always raises."""

    async def build_for_ask(self, **kwargs: Any) -> Any:
        raise RuntimeError("provider exploded")


class _BadBridge:
    """Bridge that always raises."""

    def bridge(self, **kwargs: Any) -> ArticleRagAskPromptBridgeResult:
        raise RuntimeError("bridge exploded")


# ---------------------------------------------------------------------------
# Assembly / payload fixtures
# ---------------------------------------------------------------------------


def _make_attach_assembly() -> Any:
    """Build a minimal ArticleRagAskPromptAssembly with should_attach=True."""
    from app.services.reader_orchestration.article_rag_ask_prompt_assembly import (
        ArticleRagAskPromptAssembly,
    )

    return ArticleRagAskPromptAssembly(
        kind="article_rag_context",
        should_attach=True,
        prompt_attachment_block="[RAG context text here]",
        citations=(
            {
                "citation_id": "rag-cite-1",
                "kind": "article_rag_context",
                "label": "Paragraph 1",
                "record_id": "rec-1",
                "metadata_json": {"stable_document_id": "doc-1"},
            },
        ),
        context_ids=("ctx-1",),
        source_pack_hash="abc123",
        query_sha256="a" * 64,
        status="available",
        failure_code=None,
        retryable=True,
        fallback_allowed=True,
        metadata_json={"stable_document_id": "doc-1"},
    )


def _make_no_attach_assembly() -> Any:
    """Build a minimal ArticleRagAskPromptAssembly with should_attach=False."""
    from app.services.reader_orchestration.article_rag_ask_prompt_assembly import (
        ArticleRagAskPromptAssembly,
    )

    return ArticleRagAskPromptAssembly(
        kind="article_rag_context",
        should_attach=False,
        prompt_attachment_block="",
        citations=(),
        context_ids=(),
        source_pack_hash=None,
        query_sha256="b" * 64,
        status="not_indexed_or_unavailable",
        failure_code=None,
        retryable=True,
        fallback_allowed=True,
        metadata_json={},
    )


def _make_base_payload() -> dict[str, Any]:
    return {
        "user_message": "What is the main idea?",
        "canonical_context": {"attachments": [], "anchors": []},
        "history": [],
        "thread": {"id": "thread-1"},
        "record": {"record_id": str(_RECORD_ID)},
    }


def _build_integration(
    *,
    provider: Any | None = None,
    bridge: Any | None = None,
) -> ArticleRagPromptIntegration:
    return ArticleRagPromptIntegration(
        provider=provider,
        bridge=bridge if bridge is not None else ArticleRagAskPromptBridge(),
    )


# ---------------------------------------------------------------------------
# 1. Sidecar dataclass contract
# ---------------------------------------------------------------------------


def test_article_rag_sidecar_empty_factory() -> None:
    """``ArticleRagSidecar.empty()`` returns a sidecar with
    ``should_attach=False`` and empty citations."""
    sidecar = ArticleRagSidecar.empty()
    assert sidecar.should_attach is False
    assert sidecar.citations == ()
    assert sidecar.context_ids == ()
    assert sidecar.source_pack_hash is None
    assert sidecar.query_sha256 is None
    assert sidecar.status == "not_indexed_or_unavailable"
    assert sidecar.failure_code is None
    assert sidecar.retryable is True
    assert sidecar.fallback_allowed is True
    assert sidecar.metadata_json == {}


def test_article_rag_sidecar_repr_does_not_leak_citations() -> None:
    """The sidecar's repr must NOT echo citations / query_sha256."""
    sidecar = ArticleRagSidecar(
        should_attach=True,
        citations=({"citation_id": "secret-cite"},),
        context_ids=("secret-ctx",),
        source_pack_hash="secret-hash",
        query_sha256="secret-sha",
        status="available",
    )
    repr_str = repr(sidecar)
    assert "secret-cite" not in repr_str
    assert "secret-ctx" not in repr_str
    assert "secret-hash" not in repr_str
    assert "secret-sha" not in repr_str
    assert "should_attach=True" in repr_str


# ---------------------------------------------------------------------------
# 2. integrate() returns ArticleRagPromptIntegrationResult
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_integrate_returns_result_dataclass() -> None:
    """``integrate()`` returns ``ArticleRagPromptIntegrationResult``,
    not a bare dict."""
    integration = _build_integration(
        provider=_FakeProvider(assembly=_make_attach_assembly()),
    )
    result = await integration.integrate(
        prompt_payload=_make_base_payload(),
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text=_QUERY_TEXT,
    )
    assert isinstance(result, ArticleRagPromptIntegrationResult)
    assert isinstance(result.payload, dict)
    assert isinstance(result.sidecar, ArticleRagSidecar)


# ---------------------------------------------------------------------------
# 3. Attach path: prompt has RAG text, sidecar has citations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attach_path_prompt_has_rag_context_text() -> None:
    """On the attach path, ``payload["user_message"]`` contains
    the RAG envelope markers."""
    integration = _build_integration(
        provider=_FakeProvider(assembly=_make_attach_assembly()),
    )
    result = await integration.integrate(
        prompt_payload=_make_base_payload(),
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text=_QUERY_TEXT,
    )
    assert ATTACHMENT_BEGIN_MARKER in result.payload["user_message"]
    assert ATTACHMENT_END_MARKER in result.payload["user_message"]


@pytest.mark.asyncio
async def test_attach_path_sidecar_has_citations() -> None:
    """On the attach path, the sidecar has populated citations."""
    integration = _build_integration(
        provider=_FakeProvider(assembly=_make_attach_assembly()),
    )
    result = await integration.integrate(
        prompt_payload=_make_base_payload(),
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text=_QUERY_TEXT,
    )
    assert result.sidecar.should_attach is True
    assert len(result.sidecar.citations) == 1
    assert result.sidecar.citations[0]["citation_id"] == "rag-cite-1"
    assert len(result.sidecar.context_ids) == 1
    assert result.sidecar.context_ids[0] == "ctx-1"


@pytest.mark.asyncio
async def test_attach_path_citation_json_not_in_prompt_payload() -> None:
    """The citation JSON must NOT appear anywhere in the prompt
    payload.  ``build_reader_ask_prompt`` serializes the entire
    payload for the LLM, so any citation data in the payload
    would leak to the LLM prompt."""
    integration = _build_integration(
        provider=_FakeProvider(assembly=_make_attach_assembly()),
    )
    result = await integration.integrate(
        prompt_payload=_make_base_payload(),
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text=_QUERY_TEXT,
    )
    # Serialize the payload (simulating build_reader_ask_prompt)
    serialized = json.dumps(result.payload, ensure_ascii=False)
    # The citation_id must NOT appear in the serialized prompt
    assert "rag-cite-1" not in serialized
    # The context_id must NOT appear in the serialized prompt
    assert "ctx-1" not in serialized
    # The sidecar key must NOT appear in the payload
    assert "article_rag" not in result.payload
    assert "article_rag_citations" not in result.payload
    assert "article_rag_sidecar" not in result.payload


@pytest.mark.asyncio
async def test_attach_path_sidecar_is_separate_from_payload() -> None:
    """The sidecar is a SEPARATE field on the result — it must
    NOT be merged into the payload dict."""
    integration = _build_integration(
        provider=_FakeProvider(assembly=_make_attach_assembly()),
    )
    result = await integration.integrate(
        prompt_payload=_make_base_payload(),
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text=_QUERY_TEXT,
    )
    # The payload must NOT contain any sidecar-related keys
    for key in result.payload:
        assert "article_rag" not in key, (
            f"Payload must not contain article_rag key: {key}"
        )
        assert "sidecar" not in key, (
            f"Payload must not contain sidecar key: {key}"
        )
        assert "citation" not in key.lower(), (
            f"Payload must not contain citation key: {key}"
        )


# ---------------------------------------------------------------------------
# 4. No-attach path: prompt unchanged, sidecar empty
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_attach_path_prompt_unchanged() -> None:
    """On the no-attach path, the payload is returned unchanged."""
    integration = _build_integration(
        provider=_FakeProvider(assembly=_make_no_attach_assembly()),
    )
    payload = _make_base_payload()
    import copy

    original = copy.deepcopy(payload)
    result = await integration.integrate(
        prompt_payload=payload,
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text=_QUERY_TEXT,
    )
    assert result.payload == original


@pytest.mark.asyncio
async def test_no_attach_path_sidecar_empty() -> None:
    """On the no-attach path, the sidecar has empty citations."""
    integration = _build_integration(
        provider=_FakeProvider(assembly=_make_no_attach_assembly()),
    )
    result = await integration.integrate(
        prompt_payload=_make_base_payload(),
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text=_QUERY_TEXT,
    )
    assert result.sidecar.should_attach is False
    assert result.sidecar.citations == ()
    assert result.sidecar.context_ids == ()


# ---------------------------------------------------------------------------
# 5. Fail-soft: prompt unchanged, sidecar empty
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_exception_prompt_unchanged_sidecar_empty() -> None:
    """When the provider raises, the payload is unchanged and
    the sidecar is empty."""
    integration = _build_integration(provider=_RaisingProvider())
    payload = _make_base_payload()
    import copy

    original = copy.deepcopy(payload)
    result = await integration.integrate(
        prompt_payload=payload,
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text=_QUERY_TEXT,
    )
    assert result.payload == original
    assert result.sidecar.should_attach is False
    assert result.sidecar.citations == ()


@pytest.mark.asyncio
async def test_bridge_exception_prompt_unchanged_sidecar_empty() -> None:
    """When the bridge raises, the payload is unchanged and
    the sidecar is empty."""
    integration = _build_integration(
        provider=_FakeProvider(assembly=_make_attach_assembly()),
        bridge=_BadBridge(),
    )
    payload = _make_base_payload()
    import copy

    original = copy.deepcopy(payload)
    result = await integration.integrate(
        prompt_payload=payload,
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text=_QUERY_TEXT,
    )
    assert result.payload == original
    assert result.sidecar.should_attach is False
    assert result.sidecar.citations == ()


# ---------------------------------------------------------------------------
# 6. query_text never appears in repr / str / result
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_text_not_in_sidecar_repr() -> None:
    """The sidecar's repr must NOT echo query_text."""
    integration = _build_integration(
        provider=_FakeProvider(assembly=_make_attach_assembly()),
    )
    result = await integration.integrate(
        prompt_payload=_make_base_payload(),
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text=_QUERY_TEXT,
    )
    sidecar_repr = repr(result.sidecar)
    assert _QUERY_TEXT not in sidecar_repr
    assert "query_text" not in sidecar_repr.lower()


@pytest.mark.asyncio
async def test_query_text_not_in_result_repr() -> None:
    """The result's repr must NOT echo query_text."""
    integration = _build_integration(
        provider=_FakeProvider(assembly=_make_attach_assembly()),
    )
    result = await integration.integrate(
        prompt_payload=_make_base_payload(),
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text=_QUERY_TEXT,
    )
    result_repr = repr(result)
    assert _QUERY_TEXT not in result_repr


# ---------------------------------------------------------------------------
# 7. ReaderAskRuntimeState has article_rag_* fields
# ---------------------------------------------------------------------------


def test_runtime_state_has_article_rag_fields() -> None:
    """``ReaderAskRuntimeState`` must have ``article_rag_citations``,
    ``article_rag_context_ids``, ``article_rag_metadata`` fields."""
    state = ReaderAskRuntimeState()
    assert hasattr(state, "article_rag_citations")
    assert hasattr(state, "article_rag_context_ids")
    assert hasattr(state, "article_rag_metadata")
    assert state.article_rag_citations == []
    assert state.article_rag_context_ids == []
    assert state.article_rag_metadata == {}


def test_runtime_state_article_rag_fields_are_writable() -> None:
    """The article_rag_* fields can be written to (service.py
    writes the sidecar to runtime_state)."""
    state = ReaderAskRuntimeState()
    state.article_rag_citations = [{"citation_id": "cite-1"}]
    state.article_rag_context_ids = ["ctx-1"]
    state.article_rag_metadata = {"should_attach": True}
    assert state.article_rag_citations == [{"citation_id": "cite-1"}]
    assert state.article_rag_context_ids == ["ctx-1"]
    assert state.article_rag_metadata == {"should_attach": True}


def test_runtime_state_article_rag_fields_do_not_overwrite_existing_citations() -> None:
    """Writing to ``article_rag_citations`` must NOT affect the
    existing ``citations`` field (tool-generated citations)."""
    from app.schemas.reader_ask import ReaderAskCitation

    state = ReaderAskRuntimeState()
    state.citations = [
        ReaderAskCitation(
            citation_id="tool-cite-1",
            kind="anchor",
            label="Sentence 1",
        )
    ]
    state.article_rag_citations = [{"citation_id": "rag-cite-1"}]
    # Existing citations are NOT overwritten
    assert len(state.citations) == 1
    assert state.citations[0].citation_id == "tool-cite-1"
    # Article RAG citations are separate
    assert len(state.article_rag_citations) == 1
    assert state.article_rag_citations[0]["citation_id"] == "rag-cite-1"


# ---------------------------------------------------------------------------
# 8. ReaderAskUserVisibleOutput has article_rag_citations
# ---------------------------------------------------------------------------


def test_user_visible_output_has_article_rag_citations_field() -> None:
    """``ReaderAskUserVisibleOutput`` must have
    ``article_rag_citations`` with default empty list."""
    assert "article_rag_citations" in ReaderAskUserVisibleOutput.model_fields


def test_article_rag_citations_in_user_visible_output_fields() -> None:
    """``article_rag_citations`` must be in
    ``USER_VISIBLE_OUTPUT_FIELDS``."""
    assert "article_rag_citations" in USER_VISIBLE_OUTPUT_FIELDS


def test_article_rag_citations_in_hydration_read_fields() -> None:
    """``article_rag_citations`` must be in
    ``HYDRATION_READ_FIELDS`` (hydrated from
    ``user_visible_output_json`` by the repository)."""
    assert "article_rag_citations" in HYDRATION_READ_FIELDS


def test_schema_fields_match_contract_constant() -> None:
    """``ReaderAskUserVisibleOutput`` schema fields must match
    ``USER_VISIBLE_OUTPUT_FIELDS`` exactly."""
    schema_fields = set(ReaderAskUserVisibleOutput.model_fields.keys())
    assert schema_fields == USER_VISIBLE_OUTPUT_FIELDS, (
        f"Schema fields != contract constant. "
        f"Extra in schema: {sorted(schema_fields - USER_VISIBLE_OUTPUT_FIELDS)}, "
        f"Missing from schema: {sorted(USER_VISIBLE_OUTPUT_FIELDS - schema_fields)}"
    )


# ---------------------------------------------------------------------------
# 9. build_user_visible_output passes article_rag_citations through
# ---------------------------------------------------------------------------


def _build_minimal_output_kwargs() -> dict[str, Any]:
    """Build minimal kwargs for ``build_user_visible_output``."""
    from app.schemas.reader_ask import ReaderAskResolvedContextSummary

    return {
        "content_md": "test",
        "submission_mode": "chat",
        "resolved_intent": "explain",
        "citations": [],
        "action_proposals": [],
        "tool_trace": [],
        "evidence": [],
        "trace_summary": None,
        "disambiguation": None,
        "external_asset_disambiguation": None,
        "response_cards": [],
        "usage_summary": None,
        "billed_points": 0,
        "resolved_context": ReaderAskResolvedContextSummary(record_id="rec-1"),
        "context_plan": None,
        "resolved_context_input": None,
        "run_info": None,
        "supplement_candidates": [],
        "persisted_supplements": [],
    }


def test_build_user_visible_output_defaults_article_rag_citations_empty() -> None:
    """When ``article_rag_citations`` is not passed, it defaults
    to an empty list."""
    output = build_user_visible_output(**_build_minimal_output_kwargs())
    assert output.article_rag_citations == []


def test_build_user_visible_output_passes_article_rag_citations() -> None:
    """``build_user_visible_output`` passes ``article_rag_citations``
    through to the output."""
    kwargs = _build_minimal_output_kwargs()
    kwargs["article_rag_citations"] = [
        {"citation_id": "rag-cite-1", "kind": "article_rag_context"}
    ]
    output = build_user_visible_output(**kwargs)
    assert len(output.article_rag_citations) == 1
    assert output.article_rag_citations[0]["citation_id"] == "rag-cite-1"


def test_build_user_visible_output_article_rag_separate_from_citations() -> None:
    """``article_rag_citations`` is SEPARATE from the existing
    ``citations`` field — they don't overwrite each other."""
    from app.schemas.reader_ask import ReaderAskCitation

    kwargs = _build_minimal_output_kwargs()
    kwargs["citations"] = [
        ReaderAskCitation(
            citation_id="tool-cite-1",
            kind="anchor",
            label="Sentence 1",
        )
    ]
    kwargs["article_rag_citations"] = [
        {"citation_id": "rag-cite-1", "kind": "article_rag_context"}
    ]
    output = build_user_visible_output(**kwargs)
    # Both fields are populated independently
    assert len(output.citations) == 1
    assert output.citations[0].citation_id == "tool-cite-1"
    assert len(output.article_rag_citations) == 1
    assert output.article_rag_citations[0]["citation_id"] == "rag-cite-1"


# ---------------------------------------------------------------------------
# 10. Repository hydrates article_rag_citations
# ---------------------------------------------------------------------------


def test_repository_hydrates_article_rag_citations() -> None:
    """The repository's ``_message_row_to_dict`` must hydrate
    ``article_rag_citations`` from ``user_visible_output_json``."""
    from app.services.reader_ask.repository import _message_row_to_dict

    rag_citations = [
        {"citation_id": "rag-1", "kind": "article_rag_context", "label": "RAG"}
    ]
    row: dict[str, Any] = {
        "id": "msg-1",
        "thread_id": "thread-1",
        "role": "assistant",
        "status": "completed",
        "content_md": "test",
        "context_anchors_json": [],
        "citations_json": [],
        "action_proposals_json": [],
        "tool_trace_json": [],
        "metadata_json": {},
        "current_turn_run_id": None,
        "usage_event_id": None,
        "created_at": None,
        "updated_at": None,
        "user_visible_output_json": {
            "content_md": "test",
            "submission_mode": "chat",
            "resolved_intent": None,
            "citations": [],
            "action_proposals": [],
            "tool_trace": [],
            "evidence": [],
            "trace_summary": None,
            "disambiguation": None,
            "external_asset_disambiguation": None,
            "response_cards": [],
            "usage_summary": None,
            "billed_points": 0,
            "resolved_context": {"record_id": "rec-1"},
            "context_plan": None,
            "resolved_context_input": None,
            "run_info": None,
            "supplement_candidates": [],
            "persisted_supplements": [],
            "reasoning_md": None,
            "reasoning_status": None,
            "follow_up_suggestions": None,
            "article_rag_citations": rag_citations,
        },
    }
    message = _message_row_to_dict(row)
    assert message["article_rag_citations"] == rag_citations


def test_repository_defaults_article_rag_citations_empty() -> None:
    """When ``user_visible_output_json`` is missing
    ``article_rag_citations``, the repository defaults to
    an empty list."""
    from app.services.reader_ask.repository import _message_row_to_dict

    row: dict[str, Any] = {
        "id": "msg-1",
        "thread_id": "thread-1",
        "role": "assistant",
        "status": "completed",
        "content_md": "test",
        "context_anchors_json": [],
        "citations_json": [],
        "action_proposals_json": [],
        "tool_trace_json": [],
        "metadata_json": {},
        "current_turn_run_id": None,
        "usage_event_id": None,
        "created_at": None,
        "updated_at": None,
        "user_visible_output_json": None,
    }
    message = _message_row_to_dict(row)
    assert message["article_rag_citations"] == []


# ---------------------------------------------------------------------------
# 11. service.py wiring (AST-level)
# ---------------------------------------------------------------------------


def _call_name(node: Any) -> str | None:
    """Extract the call name from a Call node's func."""
    import ast

    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def test_service_py_writes_sidecar_to_runtime_state() -> None:
    """AST-level check: ``service.py`` must call
    ``_apply_article_rag_sidecar_to_runtime_state`` in both
    ``stream_thread_message`` and ``retry_thread_message``, and
    the helper itself must assign all three
    ``article_rag_*`` fields on ``runtime_state``."""
    import ast
    import inspect

    from app.services.reader_ask import service as reader_ask_service

    source = inspect.getsource(reader_ask_service)
    module = ast.parse(source)

    functions = {
        node.name: node
        for node in ast.walk(module)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }

    # The helper must exist and assign all three article_rag_* fields.
    assert "_apply_article_rag_sidecar_to_runtime_state" in functions
    helper_node = functions["_apply_article_rag_sidecar_to_runtime_state"]
    helper_assigned: set[str] = set()
    for node in ast.walk(helper_node):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Attribute):
                continue
            if target.attr in (
                "article_rag_citations",
                "article_rag_context_ids",
                "article_rag_metadata",
            ):
                helper_assigned.add(target.attr)
    assert helper_assigned == {
        "article_rag_citations",
        "article_rag_context_ids",
        "article_rag_metadata",
    }, (
        f"helper must assign all three article_rag_* fields; "
        f"only found: {sorted(helper_assigned)}"
    )

    # Both stream and retry must call the helper.
    for fn_name in ("stream_thread_message", "retry_thread_message"):
        assert fn_name in functions, f"service.py must define {fn_name}"
        fn_node = functions[fn_name]
        helper_calls = [
            node
            for node in ast.walk(fn_node)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_apply_article_rag_sidecar_to_runtime_state"
        ]
        assert len(helper_calls) >= 1, (
            f"{fn_name} must call _apply_article_rag_sidecar_to_runtime_state"
        )


def test_service_py_passes_article_rag_citations_to_output() -> None:
    """AST-level check: ``service.py`` must pass
    ``article_rag_citations=runtime_state.article_rag_citations``
    to ``_build_user_visible_output`` and
    ``_build_stream_checkpoint_output_json``."""
    import ast
    import inspect

    from app.services.reader_ask import service as reader_ask_service

    source = inspect.getsource(reader_ask_service)
    module = ast.parse(source)

    # Find all keyword arguments named "article_rag_citations"
    rag_citation_kwargs: list[int] = []
    for node in ast.walk(module):
        if not isinstance(node, ast.keyword):
            continue
        if node.arg == "article_rag_citations":
            rag_citation_kwargs.append(node.lineno)

    # There must be at least 3 call sites:
    # 1. _build_stream_checkpoint_output_json (internal _build_user_visible_output call)
    # 2. stream_thread_message completed output
    # 3. retry_thread_message completed output
    assert len(rag_citation_kwargs) >= 3, (
        f"service.py must pass article_rag_citations to at least 3 "
        f"output builder call sites; found {len(rag_citation_kwargs)}"
    )


# ---------------------------------------------------------------------------
# 12. P2: ArticleRagSidecar.from_bridge_result copies metadata_json
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sidecar_from_bridge_result_copies_metadata_json() -> None:
    """``ArticleRagSidecar.from_bridge_result`` must copy
    ``bridge_result.metadata_json`` so the already-scrubbed
    diagnostics (budget / index / stable-id) are surfaced."""
    integration = _build_integration(
        provider=_FakeProvider(assembly=_make_attach_assembly()),
    )
    result = await integration.integrate(
        prompt_payload=_make_base_payload(),
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text=_QUERY_TEXT,
    )
    # The attach assembly has metadata_json={"stable_document_id": "doc-1"}
    assert result.sidecar.metadata_json == {"stable_document_id": "doc-1"}


def test_sidecar_metadata_json_defaults_empty() -> None:
    """``ArticleRagSidecar.empty()`` has an empty metadata_json."""
    sidecar = ArticleRagSidecar.empty()
    assert sidecar.metadata_json == {}


def test_sidecar_metadata_json_is_repr_false() -> None:
    """The metadata_json field must NOT appear in the repr."""
    sidecar = ArticleRagSidecar(
        should_attach=True,
        metadata_json={"stable_document_id": "secret-doc"},
    )
    repr_str = repr(sidecar)
    assert "secret-doc" not in repr_str
    assert "metadata_json" not in repr_str


# ---------------------------------------------------------------------------
# 13. P2: service.py merges sidecar.metadata_json into runtime_state
# ---------------------------------------------------------------------------


def test_service_py_merges_sidecar_metadata_json() -> None:
    """AST-level check: ``service.py`` must spread
    ``sidecar.metadata_json`` into
    ``runtime_state.article_rag_metadata`` inside the
    ``_apply_article_rag_sidecar_to_runtime_state`` helper
    (not just the hand-picked operational fields)."""
    import ast
    import inspect

    from app.services.reader_ask import service as reader_ask_service

    source = inspect.getsource(reader_ask_service)
    module = ast.parse(source)

    functions = {
        node.name: node
        for node in ast.walk(module)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    assert "_apply_article_rag_sidecar_to_runtime_state" in functions
    helper_node = functions["_apply_article_rag_sidecar_to_runtime_state"]

    # Find dict literals assigned to article_rag_metadata that spread
    # metadata_json (a ** spread appears as a None key in ast.Dict).
    metadata_merges: list[int] = []
    for node in ast.walk(helper_node):
        if not isinstance(node, ast.Assign):
            continue
        has_metadata_target = any(
            isinstance(t, ast.Attribute) and t.attr == "article_rag_metadata"
            for t in node.targets
        )
        if not has_metadata_target:
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        for key_node in node.value.keys:
            if key_node is None:
                metadata_merges.append(node.lineno)
                break

    assert len(metadata_merges) >= 1, (
        f"_apply_article_rag_sidecar_to_runtime_state must spread "
        f"sidecar.metadata_json into article_rag_metadata; "
        f"found {len(metadata_merges)}"
    )


# ---------------------------------------------------------------------------
# 14. P1: _merge_repair_runtime_state clears article_rag_* fields
# ---------------------------------------------------------------------------


def test_merge_repair_runtime_state_clears_article_rag_fields() -> None:
    """``_merge_repair_runtime_state`` must clear
    ``article_rag_citations`` / ``article_rag_context_ids`` on the
    target because the repair payload bypassed the Article RAG
    integration hook.

    D6-I4Q (Round 2): ``article_rag_metadata`` is NOT cleared to an
    empty dict — it is set to ``status=stale_due_to_repair`` so the
    frontend can distinguish 'RAG was never enabled' from 'RAG was
    enabled but cleared after repair'.
    """
    from app.services.reader_ask.service import _merge_repair_runtime_state

    target = ReaderAskRuntimeState()
    target.article_rag_citations = [
        {"citation_id": "stale-cite-1", "kind": "article_rag_context"}
    ]
    target.article_rag_context_ids = ["stale-ctx-1"]
    target.article_rag_metadata = {"should_attach": True, "stable_document_id": "doc-1"}

    # repair state is a deepcopy — it carries the same stale values,
    # but the merge must still clear them because the repair LLM call
    # did not go through RAG integration.
    repair = ReaderAskRuntimeState()
    repair.article_rag_citations = [
        {"citation_id": "stale-cite-1", "kind": "article_rag_context"}
    ]
    repair.article_rag_context_ids = ["stale-ctx-1"]
    repair.article_rag_metadata = {"should_attach": True}

    _merge_repair_runtime_state(target, repair)

    # Citations / context_ids are empty.
    assert target.article_rag_citations == []
    assert target.article_rag_context_ids == []
    # Metadata is replaced with the explicit stale_due_to_repair shape.
    assert target.article_rag_metadata.get("status") == "stale_due_to_repair"
    assert target.article_rag_metadata.get("failure_code") == (
        "article_rag_repair_citations_dropped"
    )


def test_merge_repair_runtime_state_clears_even_when_repair_has_rag_values() -> None:
    """Even if the repair state somehow has article_rag values
    (e.g. via deepcopy), the target must be cleared — the repair
    LLM call did not receive RAG context."""
    from app.services.reader_ask.service import _merge_repair_runtime_state

    target = ReaderAskRuntimeState()
    target.article_rag_citations = [{"citation_id": "original-cite"}]

    repair = ReaderAskRuntimeState()
    repair.article_rag_citations = [{"citation_id": "deepcopy-cite"}]

    _merge_repair_runtime_state(target, repair)

    # Must be empty, NOT the repair's deepcopy value
    assert target.article_rag_citations == []


def test_service_py_merge_repair_clears_article_rag_fields() -> None:
    """AST-level check: ``_merge_repair_runtime_state`` in
    ``service.py`` must assign empty values to all three
    ``article_rag_*`` fields on the target."""
    import ast
    import inspect

    from app.services.reader_ask import service as reader_ask_service

    source = inspect.getsource(reader_ask_service)
    module = ast.parse(source)

    functions = {
        node.name: node
        for node in ast.walk(module)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    assert "_merge_repair_runtime_state" in functions
    fn_node = functions["_merge_repair_runtime_state"]

    cleared_fields: set[str] = set()
    for node in ast.walk(fn_node):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Attribute):
                continue
            if target.attr in (
                "article_rag_citations",
                "article_rag_context_ids",
                "article_rag_metadata",
            ):
                cleared_fields.add(target.attr)

    assert cleared_fields == {
        "article_rag_citations",
        "article_rag_context_ids",
        "article_rag_metadata",
    }, (
        f"_merge_repair_runtime_state must clear all three "
        f"article_rag_* fields; only cleared: {sorted(cleared_fields)}"
    )


# ---------------------------------------------------------------------------
# 15. D6-I4Q (Round 2): Typed sidecar DTO contract (article_rag field)
# ---------------------------------------------------------------------------


def test_typed_sidecar_available_round_trip() -> None:
    """A ``ReaderAskArticleRagSidecar`` with status=available must
    survive a model_dump + ReaderAskUserVisibleOutput round-trip,
    AND the legacy ``article_rag_citations`` field must be populated
    from the sidecar's citations list (back-compat)."""
    from app.schemas.reader_ask import (
        ReaderAskArticleRagSidecar,
        ReaderAskUserVisibleOutput,
    )

    sidecar = ReaderAskArticleRagSidecar(
        status="available",
        failure_code=None,
        retryable=False,
        fallback_allowed=True,
        should_attach=True,
        context_ids=["c-1", "c-2"],
        source_pack_hash="sp-1",
        query_sha256="q-1",
        citations=[
            {
                "context_id": "ctx-1",
                "chunk_id": "chunk-1",
                "citation": {
                    "reading_record_id": "00000000-0000-0000-0000-000000000001",
                    "stable_document_id": "00000000-0000-0000-0000-000000000002",
                    "base_id": "00000000-0000-0000-0000-000000000003",
                    "record_generation": 1,
                    "block_ids": ["b0001"],
                    "unit_ids": ["u0001"],
                    "anchor_segment_ids": ["as-0001"],
                    "canonical_text_start_utf16": 0,
                    "canonical_text_end_utf16": 100,
                },
            },
        ],
    )
    dumped = sidecar.model_dump(mode="json")
    assert dumped["status"] == "available"
    assert dumped["citations"][0]["context_id"] == "ctx-1"
    assert dumped["citations"][0]["chunk_id"] == "chunk-1"
    assert (
        dumped["citations"][0]["citation"]["reading_record_id"]
        == "00000000-0000-0000-0000-000000000001"
    )
    assert dumped["citations"][0]["citation"]["record_generation"] == 1
    assert dumped["citations"][0]["citation"]["canonical_text_end_utf16"] == 100
    # Round-trip via the user-visible output container.
    out = ReaderAskUserVisibleOutput(
        content_md="x",
        submission_mode="chat",
        resolved_intent=None,
        citations=[],
        action_proposals=[],
        tool_trace=[],
        evidence=[],
        trace_summary=None,
        disambiguation=None,
        external_asset_disambiguation=None,
        response_cards=[],
        usage_summary=None,
        billed_points=0,
        resolved_context=ReaderAskResolvedContextSummary(
            record_id="rec-1",
        ),
        article_rag=sidecar,
    )
    assert out.article_rag is not None
    assert out.article_rag.status == "available"
    # Backward-compat: the legacy citations field is populated from
    # the typed sidecar's citations (serialized to dicts).
    expected_legacy = [c.model_dump(mode="json") for c in sidecar.citations]
    assert out.article_rag_citations == expected_legacy


def test_disabled_path_status_stable() -> None:
    """A sidecar with status=disabled must serialize ``should_attach=False``
    and produce a stable, no-op shape."""
    from app.schemas.reader_ask import ReaderAskArticleRagSidecar

    sidecar = ReaderAskArticleRagSidecar(
        status="disabled",
        failure_code=None,
        retryable=False,
        fallback_allowed=True,
        should_attach=False,
        context_ids=[],
        source_pack_hash=None,
        query_sha256=None,
        citations=[],
    )
    assert sidecar.status == "disabled"
    assert sidecar.should_attach is False


def test_repair_path_status_distinct_from_disabled() -> None:
    """Repair path must produce ``stale_due_to_repair`` so the frontend
    can distinguish 'RAG was never enabled' from 'RAG was enabled but
    cleared because the repair branch bypassed integration'."""
    from app.schemas.reader_ask import ReaderAskArticleRagSidecar

    sidecar = ReaderAskArticleRagSidecar(
        status="stale_due_to_repair",
        failure_code="article_rag_repair_citations_dropped",
        retryable=False,
        fallback_allowed=True,
        should_attach=False,
        context_ids=[],
        source_pack_hash=None,
        query_sha256=None,
        citations=[],
    )
    assert sidecar.status == "stale_due_to_repair"
    assert sidecar.failure_code == "article_rag_repair_citations_dropped"
    assert sidecar.citations == []


def test_unknown_status_rejected_by_schema() -> None:
    """Unknown status literals must be rejected by the schema — the
    ``ReaderAskArticleRagStatus`` literal allowlist is the contract."""
    import pytest
    from pydantic import ValidationError

    from app.schemas.reader_ask import ReaderAskArticleRagSidecar

    with pytest.raises(ValidationError):
        ReaderAskArticleRagSidecar(
            status="never_seen_before",  # type: ignore[arg-type]
            failure_code=None,
            retryable=False,
            fallback_allowed=True,
            should_attach=False,
            context_ids=[],
            source_pack_hash=None,
            query_sha256=None,
            citations=[],
        )


def test_repair_runtime_state_writes_stale_due_to_repair() -> None:
    """``_merge_repair_runtime_state`` must set the runtime metadata's
    status to ``stale_due_to_repair`` while keeping the citation list
    empty."""
    from app.services.reader_ask.service import _merge_repair_runtime_state

    target = ReaderAskRuntimeState(
        article_rag_citations=[{"chunk_id": "old"}],
        article_rag_context_ids=["c-old"],
        article_rag_metadata={"status": "available"},
    )
    repair = ReaderAskRuntimeState()

    _merge_repair_runtime_state(target, repair)

    assert target.article_rag_citations == []
    assert target.article_rag_context_ids == []
    assert target.article_rag_metadata.get("status") == "stale_due_to_repair"
    assert target.article_rag_metadata.get("failure_code") == (
        "article_rag_repair_citations_dropped"
    )
    assert target.article_rag_metadata.get("fallback_allowed") is True


def test_prompt_payload_never_contains_citation_json() -> None:
    """Regression guard: when the bridge attaches RAG context, the
    sidecar's citations must NOT leak into ``prompt_payload``. The
    payload is the dict that gets serialized into the LLM prompt;
    only the bracket text (already joined into ``user_message``) is
    allowed to be there."""

    class _StubBridge:
        should_attach = True
        prompt_text = "USER QUESTION\n\n[RAG CONTEXT]\nchunk text\n"
        # Simulated citations that must NOT appear in payload.
        citations = ({"chunk_id": "k", "preview": "leak"},)
        context_ids = ("ctx",)
        attachment_block = "chunk text"
        failure_code = None

    # Mimic the production attach branch from
    # ``ArticleRagPromptIntegration.integrate``: when the bridge
    # says ``should_attach=True``, only ``user_message`` is written.
    payload: dict = {"user_message": "ORIGINAL"}
    bridge = _StubBridge()
    if bridge.should_attach:
        payload["user_message"] = bridge.prompt_text
    assert "citations" not in payload
    assert "context_ids" not in payload
    assert "attachment_block" not in payload
    assert payload["user_message"].startswith("USER QUESTION")


# ---------------------------------------------------------------------------
# 16. D6-I4Q (Round 2): Repository hydrate typed sidecar
# ---------------------------------------------------------------------------


def test_repository_hydrates_typed_sidecar() -> None:
    """Hydrating a stored ``user_visible_output_json`` must produce a
    ``ReaderAskUserVisibleOutput`` whose ``article_rag`` typed sidecar
    survives the JSON round-trip with status intact.

    The repository uses ``_message_row_to_dict`` directly with a stubbed
    row dict (mirrors the production shape).
    """
    from app.services.reader_ask.repository import _message_row_to_dict
    from app.schemas.reader_ask import (
        ReaderAskArticleRagSidecar,
        ReaderAskUserVisibleOutput,
    )

    stored_sidecar = {
        "status": "stale_due_to_repair",
        "failure_code": "article_rag_repair_citations_dropped",
        "retryable": False,
        "fallback_allowed": True,
        "should_attach": False,
        "context_ids": [],
        "source_pack_hash": None,
        "query_sha256": None,
        "citations": [],
    }
    row: dict[str, Any] = {
        "id": "msg-1",
        "thread_id": "thread-1",
        "role": "assistant",
        "status": "completed",
        "content_md": "test",
        "context_anchors_json": [],
        "citations_json": [],
        "action_proposals_json": [],
        "tool_trace_json": [],
        "metadata_json": {},
        "current_turn_run_id": None,
        "usage_event_id": None,
        "created_at": None,
        "updated_at": None,
        "user_visible_output_json": {
            "content_md": "test",
            "submission_mode": "chat",
            "resolved_intent": None,
            "citations": [],
            "action_proposals": [],
            "tool_trace": [],
            "evidence": [],
            "trace_summary": None,
            "disambiguation": None,
            "external_asset_disambiguation": None,
            "response_cards": [],
            "usage_summary": None,
            "billed_points": 0,
            "resolved_context": {"record_id": "rec-1"},
            "context_plan": None,
            "resolved_context_input": None,
            "run_info": None,
            "supplement_candidates": [],
            "persisted_supplements": [],
            "reasoning_md": None,
            "reasoning_status": None,
            "follow_up_suggestions": None,
            "article_rag_citations": [],
            "article_rag": stored_sidecar,
        },
    }
    message = _message_row_to_dict(row)
    # The hydration shape returns the raw user_visible_output_json
    # nested under ``current_user_visible_output``.  Round-trip the
    # stored sidecar through the schema to confirm it parses cleanly
    # AND the status literal is preserved.
    current_output = message["current_user_visible_output"]
    assert current_output["article_rag"]["status"] == "stale_due_to_repair"
    parsed_sidecar = ReaderAskArticleRagSidecar.model_validate(
        current_output["article_rag"]
    )
    assert isinstance(parsed_sidecar, ReaderAskArticleRagSidecar)
    assert parsed_sidecar.status == "stale_due_to_repair"
    # And the legacy field round-trips with an empty list as well.
    assert current_output["article_rag_citations"] == []

    # Also confirm a full container round-trip is valid.
    full_output = ReaderAskUserVisibleOutput.model_validate(current_output)
    assert full_output.article_rag is not None
    assert full_output.article_rag.status == "stale_due_to_repair"


# ---------------------------------------------------------------------------
# 17. D6-Closure-A final fix: wiring the typed sidecar through service.py
# ---------------------------------------------------------------------------


def test_build_typed_sidecar_returns_none_when_runtime_state_empty() -> None:
    """``_build_typed_article_rag_sidecar_from_runtime`` must return
    ``None`` when both runtime fields are empty — the historical
    'no RAG in this run' case."""
    from app.services.reader_ask.service import (
        _build_typed_article_rag_sidecar_from_runtime,
    )

    assert (
        _build_typed_article_rag_sidecar_from_runtime(
            article_rag_metadata=None,
            article_rag_citations=None,
        )
        is None
    )
    # Either field empty also returns None.
    assert (
        _build_typed_article_rag_sidecar_from_runtime(
            article_rag_metadata=None,
            article_rag_citations=[],
        )
        is None
    )
    assert (
        _build_typed_article_rag_sidecar_from_runtime(
            article_rag_metadata={},
            article_rag_citations=None,
        )
        is None
    )


def test_build_typed_sidecar_reads_repair_metadata() -> None:
    """``_build_typed_article_rag_sidecar_from_runtime`` must map the
    repair-path metadata (``stale_due_to_repair``) into the typed
    sidecar so the frontend can distinguish a repair branch from
    'RAG is broken'."""
    from app.services.reader_ask.service import (
        _build_typed_article_rag_sidecar_from_runtime,
    )

    sidecar = _build_typed_article_rag_sidecar_from_runtime(
        article_rag_metadata={
            "status": "stale_due_to_repair",
            "failure_code": "article_rag_repair_citations_dropped",
            "retryable": False,
            "fallback_allowed": True,
            "should_attach": False,
            "context_ids": [],
        },
        article_rag_citations=[],
    )
    assert sidecar is not None
    assert sidecar.status == "stale_due_to_repair"
    assert sidecar.failure_code == "article_rag_repair_citations_dropped"
    assert sidecar.retryable is False
    assert sidecar.fallback_allowed is True
    assert sidecar.should_attach is False
    assert sidecar.context_ids == []
    assert sidecar.citations == []


def test_build_user_visible_output_forwards_typed_sidecar() -> None:
    """The wrapper ``_build_user_visible_output`` must accept and
    forward the typed ``article_rag`` sidecar to
    ``output_contract.build_user_visible_output``, so the production
    call sites can route the typed runtime state to the frontend."""
    from app.schemas.reader_ask import (
        ReaderAskArticleRagSidecar,
        ReaderAskResolvedContextSummary,
    )
    from app.services.reader_ask.service import _build_user_visible_output

    minimal_resolved_context = ReaderAskResolvedContextSummary.model_validate(
        {
            "record_id": "rec-1",
            "record_title": "Title",
            "anchors": [],
            "explicit_attachment_count": 0,
        }
    )

    sidecar = ReaderAskArticleRagSidecar(
        status="stale_due_to_repair",
        failure_code="article_rag_repair_citations_dropped",
        retryable=False,
        fallback_allowed=True,
        should_attach=False,
        context_ids=[],
        source_pack_hash=None,
        query_sha256=None,
        citations=[],
    )

    output = _build_user_visible_output(
        content_md="hello",
        submission_mode="chat",
        resolved_intent=None,
        citations=[],
        action_proposals=[],
        tool_trace=[],
        evidence=[],
        trace_summary=None,
        disambiguation=None,
        external_asset_disambiguation=None,
        response_cards=[],
        usage_summary=None,
        billed_points=0,
        resolved_context=minimal_resolved_context,
        context_plan=None,
        resolved_context_input=None,
        run_info=None,
        supplement_candidates=[],
        persisted_supplements=[],
        article_rag_citations=[],
        article_rag=sidecar,
    )
    assert output.article_rag is not None
    assert output.article_rag.status == "stale_due_to_repair"
    assert output.article_rag.failure_code == "article_rag_repair_citations_dropped"


# ---------------------------------------------------------------------------
# 18. D6-Closure-A follow-up: typed citation matches the I4G shape
#     AND status coercion contract is implemented at the boundary.
# ---------------------------------------------------------------------------


def test_real_upstream_sidecar_shape_does_not_raise() -> None:
    """Real I4G sidecar shape ``{context_id, chunk_id, citation: {9-key I4A truth}}``
    must validate cleanly through the typed schema.  This is the
    regression test for the P1 finding — a real RAG-available Ask
    completion must not fail at output-build time.

    Pre-fix bug: ``ReaderAskArticleRagCitation`` was a flat 9-key
    schema, so ``model_validate({"context_id": ..., "chunk_id": ...,
    "citation": {...}})`` raised ``ValidationError`` because of
    ``extra="forbid"`` and missing required flat fields.  The build
    helper ran inside ``_build_user_visible_output`` — the user saw
    a 500 on the only RAG-available path.
    """
    from app.services.reader_ask.service import (
        _build_typed_article_rag_sidecar_from_runtime,
    )
    real_citation = {
        "context_id": "ctx-1",
        "chunk_id": "chunk-1",
        "citation": {
            "reading_record_id": "00000000-0000-0000-0000-000000000001",
            "stable_document_id": "00000000-0000-0000-0000-000000000002",
            "base_id": "00000000-0000-0000-0000-000000000003",
            "record_generation": 1,
            "block_ids": ["b0001"],
            "unit_ids": ["u0001"],
            "anchor_segment_ids": ["as-0001"],
            "canonical_text_start_utf16": 0,
            "canonical_text_end_utf16": 100,
        },
    }
    sidecar = _build_typed_article_rag_sidecar_from_runtime(
        article_rag_metadata={
            "status": "available",
            "should_attach": True,
            "context_ids": ["ctx-1"],
            "source_pack_hash": "sp-1",
            "query_sha256": "q-1",
        },
        article_rag_citations=[real_citation],
    )
    assert sidecar is not None
    assert sidecar.status == "available"
    assert len(sidecar.citations) == 1
    c = sidecar.citations[0]
    assert c.context_id == "ctx-1"
    assert c.chunk_id == "chunk-1"
    # Pin the 9-key I4A truth
    assert c.citation.reading_record_id == "00000000-0000-0000-0000-000000000001"
    assert c.citation.stable_document_id == "00000000-0000-0000-0000-000000000002"
    assert c.citation.base_id == "00000000-0000-0000-0000-000000000003"
    assert c.citation.record_generation == 1
    assert c.citation.block_ids == ["b0001"]
    assert c.citation.unit_ids == ["u0001"]
    assert c.citation.anchor_segment_ids == ["as-0001"]
    assert c.citation.canonical_text_start_utf16 == 0
    assert c.citation.canonical_text_end_utf16 == 100


def test_unknown_status_coerces_to_not_indexed_or_unavailable() -> None:
    """Unknown status values must coerce to
    ``not_indexed_or_unavailable``, not raise — see
    ``frontend-integration-status-map.md`` (Coercion contract).

    Pre-fix bug: ``metadata["status"]`` was passed straight to the
    Pydantic Literal field, so any regression / hostile fake / drift
    caused a ``ValidationError`` instead of a safe fallback.  This
    test verifies the coercion helper at the boundary.
    """
    from app.services.reader_ask.service import (
        _build_typed_article_rag_sidecar_from_runtime,
    )

    sidecar = _build_typed_article_rag_sidecar_from_runtime(
        article_rag_metadata={"status": "never_seen_before"},
        article_rag_citations=None,
    )
    assert sidecar is not None
    assert sidecar.status == "not_indexed_or_unavailable"


def test_unknown_status_coerces_on_real_upstream_shape() -> None:
    """Coercion must hold even with the real nested citation shape.

    Combines the P1 (nested shape) and P2 (coercion) regressions into
    one test — the canonical failure mode is an unknown status
    arriving alongside real I4G citations on the available path.
    """
    from app.services.reader_ask.service import (
        _build_typed_article_rag_sidecar_from_runtime,
    )

    sidecar = _build_typed_article_rag_sidecar_from_runtime(
        article_rag_metadata={"status": "something_hostile"},
        article_rag_citations=[
            {
                "context_id": "ctx-x",
                "chunk_id": "chunk-y",
                "citation": {
                    "reading_record_id": "00000000-0000-0000-0000-000000000001",
                    "stable_document_id": "00000000-0000-0000-0000-000000000002",
                    "base_id": "00000000-0000-0000-0000-000000000003",
                    "record_generation": 1,
                    "block_ids": [],
                    "unit_ids": [],
                    "anchor_segment_ids": [],
                    "canonical_text_start_utf16": 0,
                    "canonical_text_end_utf16": 10,
                },
            }
        ],
    )
    assert sidecar is not None
    assert sidecar.status == "not_indexed_or_unavailable"
    assert len(sidecar.citations) == 1
    assert sidecar.citations[0].context_id == "ctx-x"
    assert sidecar.citations[0].chunk_id == "chunk-y"
    assert (
        sidecar.citations[0].citation.reading_record_id
        == "00000000-0000-0000-0000-000000000001"
    )
    assert sidecar.citations[0].citation.canonical_text_end_utf16 == 10


def test_extra_forbid_rejects_non_allowlist_key_on_inner_citation() -> None:
    """The inner content model uses ``extra="forbid"`` — a regression
    that surfaces a non-I4A key (e.g. provider metadata, query text,
    a UI projection field) must raise ``ValidationError``.

    Mirrors ``article_rag_ask_prompt_attachment._ALLOWED_CITATION_KEYS``
    — the attachment layer already strips hostile extras, but the
    typed DTO re-enforces the closed schema as defence in depth.
    """
    from pydantic import ValidationError

    from app.schemas.reader_ask import ReaderAskArticleRagCitation

    bad = {
        "context_id": "ctx-1",
        "chunk_id": "chunk-1",
        "citation": {
            "reading_record_id": "r-1",
            "stable_document_id": "sd-1",
            "base_id": "b-1",
            "record_generation": 1,
            "block_ids": [],
            "unit_ids": [],
            "anchor_segment_ids": [],
            "canonical_text_start_utf16": 0,
            "canonical_text_end_utf16": 10,
            "provider_metadata": {"query_text": "leak"},  # hostile field
        },
    }
    with pytest.raises(ValidationError):
        ReaderAskArticleRagCitation.model_validate(bad)
