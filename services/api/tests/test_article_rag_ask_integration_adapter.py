# task-history: D6-I4J (renamed from test_d6_i4j_article_rag_ask_integration_adapter.py)
"""D6-I4J: tests for Reader Ask RAG integration adapter.

Covers:
  * available attachment → include_in_prompt=True with verbatim
    prompt_text / citations / context_ids / source_pack_hash.
  * disabled / empty / not_indexed / composer_rejected → include
    in prompt = False, status / failure_code preserved.
  * unexpected attachment service exception → fail-soft, no leak
    of query_text or upstream message.
  * malformed attachment object → fail-soft.
  * metadata_json strict allowlist: no query_text /
    provider_metadata / vector / projection keys.
  * citations stay separate from prompt_text; adapter does NOT
    parse prompt_text.
  * stable ids / budget / source_pack_hash propagated.
  * no DB / network / LLM.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.services.reader_orchestration.article_rag_ask_integration_adapter import (
    DEFAULT_INTEGRATION_LIMIT,
    DEFAULT_INTEGRATION_MAX_CONTEXT_CHARS,
    FAILURE_CODE_INTEGRATION_UNEXPECTED_ERROR,
    SEGMENT_KIND,
    ArticleRagAskIntegrationAdapter,
    ArticleRagAskPromptSegment,
)
from app.services.reader_orchestration.article_rag_ask_prompt_attachment import (
    ArticleRagAskPromptAttachment,
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
_PROMPT_TEXT = (
    "[rag-1] rank=1 score=0.950000\n"
    "alpha content\n\n"
    "[rag-2] rank=2 score=0.850000\n"
    "beta content"
)


@dataclass
class _FakeAttachmentService:
    """Stand-in for :class:`ArticleRagAskPromptAttachmentService`.

    Configure either ``attachment_factory`` (happy path) or
    ``raise_exc`` (error path) — never both.  ``raise_exc``
    takes precedence.  Records every call.
    """

    calls: list[dict[str, Any]] = field(default_factory=list)
    attachment_factory: "callable | None" = None
    raise_exc: Exception | None = None

    async def build_for_ask(
        self,
        *,
        reading_record_id: uuid.UUID,
        user_id: uuid.UUID,
        query_text: str,
        enabled: bool = True,
        limit: int = DEFAULT_INTEGRATION_LIMIT,
        max_context_chars: int = DEFAULT_INTEGRATION_MAX_CONTEXT_CHARS,
    ) -> ArticleRagAskPromptAttachment:
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
        assert self.attachment_factory is not None
        return self.attachment_factory(
            reading_record_id=reading_record_id, query_text=query_text
        )


def _make_attachment(
    *,
    should_include_context: bool = True,
    status: str = "available",
    enabled: bool = True,
    prompt_context_text: str = _PROMPT_TEXT,
    citations: tuple[dict[str, Any], ...] | None = None,
    context_ids: tuple[str, ...] | None = None,
    source_pack_hash: str | None = _SOURCE_PACK_HASH,
    query_sha256: str | None = None,
    failure_code: str | None = None,
    retryable: bool = False,
    fallback_allowed: bool = True,
    omitted_hit_count: int | None = 0,
    budget_exceeded: bool | None = False,
) -> ArticleRagAskPromptAttachment:
    if citations is None:
        citations = (
            {
                "context_id": "rag-1",
                "chunk_id": "c1",
                "citation": {
                    "reading_record_id": str(_RECORD_ID),
                    "stable_document_id": str(_STABLE_DOC_ID),
                    "base_id": str(_BASE_ID),
                    "record_generation": 1,
                    "block_ids": ["block-x"],
                    "unit_ids": [],
                    "anchor_segment_ids": [],
                    "canonical_text_start_utf16": 0,
                    "canonical_text_end_utf16": 10,
                },
            },
            {
                "context_id": "rag-2",
                "chunk_id": "c2",
                "citation": {
                    "reading_record_id": str(_RECORD_ID),
                    "stable_document_id": str(_STABLE_DOC_ID),
                    "base_id": str(_BASE_ID),
                    "record_generation": 1,
                    "block_ids": ["block-y"],
                    "unit_ids": [],
                    "anchor_segment_ids": [],
                    "canonical_text_start_utf16": 11,
                    "canonical_text_end_utf16": 20,
                },
            },
        )
    if context_ids is None:
        context_ids = ("rag-1", "rag-2")
    if query_sha256 is None:
        query_sha256 = hashlib.sha256(b"hello").hexdigest()
    return ArticleRagAskPromptAttachment(
        enabled=enabled,
        status=status,
        should_include_context=should_include_context,
        fallback_allowed=fallback_allowed,
        query_sha256=query_sha256,
        prompt_context_text=prompt_context_text,
        citations=citations,
        context_ids=context_ids,
        source_pack_hash=source_pack_hash,
        failure_code=failure_code,
        retryable=retryable,
        omitted_hit_count=omitted_hit_count,
        budget_exceeded=budget_exceeded,
        reading_record_id=_RECORD_ID,
        stable_document_id=_STABLE_DOC_ID,
        base_id=_BASE_ID,
        record_generation=1,
        plan_content_sha256=_PLAN_HASH,
    )


def _build_adapter(
    *,
    attachment_service: _FakeAttachmentService | None = None,
) -> ArticleRagAskIntegrationAdapter:
    return ArticleRagAskIntegrationAdapter(
        attachment_service=attachment_service
        or _FakeAttachmentService(
            attachment_factory=lambda **kw: _make_attachment()
        )
    )


# ---------------------------------------------------------------------------
# 1. Available → include_in_prompt=True with verbatim fields
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_available_includes_context_verbatim() -> None:
    attachment_service = _FakeAttachmentService(
        attachment_factory=lambda **kw: _make_attachment(
            should_include_context=True
        )
    )
    adapter = _build_adapter(attachment_service=attachment_service)
    segment = await adapter.build_prompt_segment(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
    assert isinstance(segment, ArticleRagAskPromptSegment)
    assert segment.kind == "article_rag_context"
    assert segment.include_in_prompt is True
    # prompt_text is EXACTLY the I4I attachment's prompt_context_text.
    assert segment.prompt_text == _PROMPT_TEXT
    # Citations are copied verbatim.
    assert segment.citations == _make_attachment().citations
    # Context ids are preserved.
    assert segment.context_ids == ("rag-1", "rag-2")
    # source_pack_hash is preserved.
    assert segment.source_pack_hash == _SOURCE_PACK_HASH
    # query_sha256 is preserved.
    assert segment.query_sha256 == hashlib.sha256(b"hello").hexdigest()
    # Status / failure_code / retryable / fallback_allowed.
    assert segment.status == "available"
    assert segment.failure_code is None
    assert segment.retryable is False
    assert segment.fallback_allowed is True
    # metadata_json strict-allowlist populated.
    assert "status" in segment.metadata_json
    assert "stable_document_id" in segment.metadata_json


# ---------------------------------------------------------------------------
# 2. No-context statuses → include_in_prompt=False
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_disabled_path_no_context() -> None:
    attachment_service = _FakeAttachmentService(
        attachment_factory=lambda **kw: _make_attachment(
            should_include_context=False,
            status="disabled",
            enabled=False,
            prompt_context_text="",
            citations=(),
            context_ids=(),
            source_pack_hash=None,
        )
    )
    adapter = _build_adapter(attachment_service=attachment_service)
    segment = await adapter.build_prompt_segment(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
        enabled=False,
    )
    assert segment.include_in_prompt is False
    assert segment.kind == "article_rag_context"
    assert segment.prompt_text == ""
    assert segment.citations == ()
    assert segment.context_ids == ()
    assert segment.source_pack_hash is None
    assert segment.status == "disabled"
    assert segment.fallback_allowed is True


@pytest.mark.anyio
async def test_empty_path_no_context() -> None:
    attachment_service = _FakeAttachmentService(
        attachment_factory=lambda **kw: _make_attachment(
            should_include_context=False,
            status="empty",
            prompt_context_text="",
            citations=(),
            context_ids=(),
        )
    )
    adapter = _build_adapter(attachment_service=attachment_service)
    segment = await adapter.build_prompt_segment(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
    assert segment.status == "empty"
    assert segment.include_in_prompt is False
    assert segment.prompt_text == ""


@pytest.mark.anyio
async def test_not_indexed_path_no_context() -> None:
    attachment_service = _FakeAttachmentService(
        attachment_factory=lambda **kw: _make_attachment(
            should_include_context=False,
            status="not_indexed_or_unavailable",
            failure_code="context_no_indexed_run",
            prompt_context_text="",
            citations=(),
            context_ids=(),
        )
    )
    adapter = _build_adapter(attachment_service=attachment_service)
    segment = await adapter.build_prompt_segment(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
    assert segment.status == "not_indexed_or_unavailable"
    assert segment.include_in_prompt is False
    assert segment.failure_code == "context_no_indexed_run"


@pytest.mark.anyio
async def test_composer_rejected_path_no_context() -> None:
    attachment_service = _FakeAttachmentService(
        attachment_factory=lambda **kw: _make_attachment(
            should_include_context=False,
            status="composer_rejected",
            failure_code="ask_context_empty_text",
            prompt_context_text="",
            citations=(),
            context_ids=(),
        )
    )
    adapter = _build_adapter(attachment_service=attachment_service)
    segment = await adapter.build_prompt_segment(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
    assert segment.status == "composer_rejected"
    assert segment.include_in_prompt is False
    assert segment.failure_code == "ask_context_empty_text"


# ---------------------------------------------------------------------------
# 3. Unexpected attachment service exception
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_unexpected_attachment_exception_fails_soft() -> None:
    secret = "SECRET-QUERY-DO-NOT-LEAK-DO-NOT-LEAK"
    secret_message = (
        f"attachment service exploded with internal diagnostics: {secret}"
    )
    attachment_service = _FakeAttachmentService(
        raise_exc=RuntimeError(secret_message)
    )
    adapter = _build_adapter(attachment_service=attachment_service)
    # MUST NOT raise.
    segment = await adapter.build_prompt_segment(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text=secret,
    )
    assert segment.status == "not_indexed_or_unavailable"
    assert segment.include_in_prompt is False
    assert segment.failure_code == FAILURE_CODE_INTEGRATION_UNEXPECTED_ERROR
    assert segment.fallback_allowed is True
    assert segment.prompt_text == ""
    assert segment.citations == ()
    # The original message MUST NOT leak.
    assert secret_message not in repr(segment)
    assert secret_message not in str(segment)
    # The query text MUST NOT leak.
    assert secret not in repr(segment)
    assert secret not in str(segment)


# ---------------------------------------------------------------------------
# 4. Malformed attachment object
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_malformed_attachment_object_fails_soft() -> None:
    """A regression / hostile fake in the attachment service
    could return a non-dataclass object.  The adapter MUST
    fail-soft rather than crash."""

    class _BrokenAttachmentService:
        async def build_for_ask(self, **kwargs: Any) -> Any:
            # Return a plain dict (NOT an
            # ArticleRagAskPromptAttachment).
            return {"status": "available", "should_include_context": True}

    adapter = ArticleRagAskIntegrationAdapter(
        attachment_service=_BrokenAttachmentService()
    )
    segment = await adapter.build_prompt_segment(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
    assert segment.status == "not_indexed_or_unavailable"
    assert segment.include_in_prompt is False
    assert segment.failure_code == FAILURE_CODE_INTEGRATION_UNEXPECTED_ERROR
    assert segment.fallback_allowed is True


# ---------------------------------------------------------------------------
# 5. Missing attachment service config
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_missing_attachment_service_fails_soft() -> None:
    adapter = ArticleRagAskIntegrationAdapter()  # no attachment service
    segment = await adapter.build_prompt_segment(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
    assert segment.status == "not_indexed_or_unavailable"
    assert segment.include_in_prompt is False
    assert segment.failure_code == FAILURE_CODE_INTEGRATION_UNEXPECTED_ERROR
    assert segment.fallback_allowed is True
    assert segment.prompt_text == ""


# ---------------------------------------------------------------------------
# 6. metadata_json strict allowlist
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_metadata_json_contains_only_allowlisted_keys() -> None:
    """Defence in depth: even if the attachment carries a
    ``metadata_json``-shaped field elsewhere (e.g. on a
    regression), the segment's ``metadata_json`` is built
    exclusively from the 12 allowlisted keys.  A regression
    that surfaces a hostile key on the attachment MUST NOT
    leak through to the segment's metadata_json.
    """
    attachment_service = _FakeAttachmentService(
        attachment_factory=lambda **kw: _make_attachment(
            should_include_context=True
        )
    )
    adapter = _build_adapter(attachment_service=attachment_service)
    segment = await adapter.build_prompt_segment(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
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
    assert set(segment.metadata_json.keys()) <= expected_keys
    # Sanity: the include path populates the safe fields.
    assert segment.metadata_json["status"] == "available"
    assert str(segment.metadata_json["stable_document_id"]) == str(
        _STABLE_DOC_ID
    )
    assert segment.metadata_json["plan_content_sha256"] == _PLAN_HASH


@pytest.mark.anyio
async def test_metadata_json_excludes_forbidden_keys() -> None:
    """The segment's metadata_json MUST NOT contain:
      * ``query_text`` (only ``query_sha256`` is surfaced);
      * ``provider_metadata`` (searcher diagnostics);
      * any UI projection key (plate / markdown / dom / slate /
        ui / render / html / text / chunks);
      * ``citations`` (citations are at the top level, not in
        metadata_json).
    """
    attachment_service = _FakeAttachmentService(
        attachment_factory=lambda **kw: _make_attachment(
            should_include_context=True
        )
    )
    adapter = _build_adapter(attachment_service=attachment_service)
    segment = await adapter.build_prompt_segment(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
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
    ):
        assert forbidden not in segment.metadata_json, (
            f"forbidden key {forbidden!r} appears in metadata_json"
        )


# ---------------------------------------------------------------------------
# 7. provider_metadata / vector / projection keys cannot appear anywhere
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_provider_metadata_vector_projection_keys_absent() -> None:
    """Even if a regression in I4I / I4H / I4G surfaces a
    hostile field on the segment, the adapter's
    ``metadata_json`` allowlist + segment field allowlist MUST
    keep them out of ``repr(segment)`` / ``str(segment)``.
    """
    secret = "SECRET-INJECTED-VIA-UPSTREAM-REGRESSION-DO-NOT-LEAK"
    secret_query = "SECRET-QUERY-DO-NOT-LEAK-DO-NOT-LEAK"
    # Construct an attachment with a ``provider_metadata``
    # attribute-shaped field.  (The attachment dataclass does
    # NOT carry provider_metadata; the test simulates a
    # future regression that adds such a field, OR a hostile
    # fake that bypasses the dataclass.)
    attachment_service = _FakeAttachmentService(
        attachment_factory=lambda **kw: ArticleRagAskPromptAttachment(
            enabled=True,
            status="available",
            should_include_context=True,
            fallback_allowed=True,
            query_sha256=hashlib.sha256(secret_query.encode("utf-8")).hexdigest(),
            prompt_context_text=f"[rag-1] score=0.9\n{secret_query}",
            citations=(),
            context_ids=("rag-1",),
            source_pack_hash=_SOURCE_PACK_HASH,
            failure_code=None,
            retryable=False,
            omitted_hit_count=0,
            budget_exceeded=False,
            reading_record_id=_RECORD_ID,
            stable_document_id=_STABLE_DOC_ID,
            base_id=_BASE_ID,
            record_generation=1,
            plan_content_sha256=_PLAN_HASH,
        )
    )
    adapter = _build_adapter(attachment_service=attachment_service)
    segment = await adapter.build_prompt_segment(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text=secret_query,
    )
    # Provider / vector / projection keys are NOT in
    # metadata_json (allowlist enforcement).
    for forbidden in (
        "provider_metadata",
        "query_text",
        "plate",
        "markdown",
        "dom",
        "slate",
        "ui",
        "render_profile",
        "html",
        "text",
        "chunks",
    ):
        assert forbidden not in segment.metadata_json
    # metadata_json values MUST NOT contain the secret value
    # that would have been on a regressed ``provider_metadata``
    # field.  (Note: ``prompt_text`` MAY legitimately echo the
    # query — the prompt is built from the I4G composer output,
    # which is allowed to embed query fragments.  The
    # ``metadata_json`` path is the side that MUST be clean.)
    assert secret not in repr(segment.metadata_json)
    for value in segment.metadata_json.values():
        if isinstance(value, str):
            assert secret not in value
            assert secret_query not in value


# ---------------------------------------------------------------------------
# 8. Citations stay separate from prompt_text
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_citations_not_parsed_from_prompt_text() -> None:
    """The adapter MUST NOT re-parse ``prompt_text`` to extract
    citations.  Citations come from ``attachment.citations``
    only — even if the prompt text were to include stray
    citation-like content, the segment's citations would
    remain exactly what the attachment carried.
    """
    # Construct an attachment whose prompt_text embeds a
    # "citation-like" string.  The segment's citations MUST
    # NOT include this — only the structured ``citations``
    # tuple from the attachment.
    decoy = "DECOY-CITATION-DO-NOT-EXTRACT"
    attachment_service = _FakeAttachmentService(
        attachment_factory=lambda **kw: _make_attachment(
            should_include_context=True,
            prompt_context_text=(
                f"[rag-1] rank=1 score=0.9\n{decoy}"
            ),
            citations=(),
        )
    )
    adapter = _build_adapter(attachment_service=attachment_service)
    segment = await adapter.build_prompt_segment(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
    # The decoy appears in the prompt text (verbatim from the
    # attachment) but is NOT in the structured citations.
    assert decoy in segment.prompt_text
    assert segment.citations == ()


# ---------------------------------------------------------------------------
# 9. Stable ids + budget + source_pack_hash propagated
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_stable_ids_and_budget_propagated() -> None:
    attachment_service = _FakeAttachmentService(
        attachment_factory=lambda **kw: _make_attachment(
            should_include_context=True,
            omitted_hit_count=4,
            budget_exceeded=True,
        )
    )
    adapter = _build_adapter(attachment_service=attachment_service)
    segment = await adapter.build_prompt_segment(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
    # Stable ids surface in metadata_json.
    assert str(segment.metadata_json["stable_document_id"]) == str(
        _STABLE_DOC_ID
    )
    assert str(segment.metadata_json["base_id"]) == str(_BASE_ID)
    assert segment.metadata_json["record_generation"] == 1
    assert segment.metadata_json["plan_content_sha256"] == _PLAN_HASH
    assert segment.metadata_json["source_pack_hash"] == _SOURCE_PACK_HASH
    # Budget fields surface in metadata_json.
    assert segment.metadata_json["omitted_hit_count"] == 4
    assert segment.metadata_json["budget_exceeded"] is True


@pytest.mark.anyio
async def test_stable_ids_propagated_on_no_context_paths() -> None:
    """Stable ids MUST be echoed on no-context paths too — the
    Ask layer uses them for cache keys and log dedup.
    """
    attachment_service = _FakeAttachmentService(
        attachment_factory=lambda **kw: _make_attachment(
            should_include_context=False,
            status="empty",
            prompt_context_text="",
            citations=(),
            context_ids=(),
            source_pack_hash=None,
            omitted_hit_count=2,
            budget_exceeded=False,
        )
    )
    adapter = _build_adapter(attachment_service=attachment_service)
    segment = await adapter.build_prompt_segment(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
    assert segment.include_in_prompt is False
    assert str(segment.metadata_json["stable_document_id"]) == str(
        _STABLE_DOC_ID
    )
    assert str(segment.metadata_json["base_id"]) == str(_BASE_ID)
    assert segment.metadata_json["record_generation"] == 1
    assert segment.metadata_json["plan_content_sha256"] == _PLAN_HASH
    # source_pack_hash is None on the empty path.
    assert segment.metadata_json["source_pack_hash"] is None
    # Budget fields are echoed.
    assert segment.metadata_json["omitted_hit_count"] == 2
    assert segment.metadata_json["budget_exceeded"] is False


# ---------------------------------------------------------------------------
# 10. Parameter passthrough to attachment service
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_parameter_passthrough_to_attachment_service() -> None:
    attachment_service = _FakeAttachmentService(
        attachment_factory=lambda **kw: _make_attachment(
            should_include_context=True
        )
    )
    adapter = _build_adapter(attachment_service=attachment_service)
    await adapter.build_prompt_segment(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
        enabled=False,
        limit=12,
        max_context_chars=8000,
    )
    assert len(attachment_service.calls) == 1
    call = attachment_service.calls[0]
    assert call["enabled"] is False
    assert call["limit"] == 12
    assert call["max_context_chars"] == 8000
    assert call["reading_record_id"] == str(_RECORD_ID)
    assert call["query_text"] == "hello"


# ---------------------------------------------------------------------------
# 11. query_text never in repr/str
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_query_text_not_in_repr_or_str() -> None:
    secret = "SECRET-QUERY-DO-NOT-LEAK-DO-NOT-LEAK"
    attachment_service = _FakeAttachmentService(
        attachment_factory=lambda **kw: _make_attachment(
            should_include_context=True,
            query_sha256=hashlib.sha256(secret.encode("utf-8")).hexdigest(),
        )
    )
    adapter = _build_adapter(attachment_service=attachment_service)
    segment = await adapter.build_prompt_segment(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text=secret,
    )
    assert secret not in repr(segment)
    assert secret not in str(segment)
    # The query_sha256 is the only query-derived value on the
    # segment.
    assert (
        segment.query_sha256
        == hashlib.sha256(secret.encode("utf-8")).hexdigest()
    )


# ---------------------------------------------------------------------------
# 12. Determinism
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_segment_deterministic_for_same_input() -> None:
    async def _run_once() -> ArticleRagAskPromptSegment:
        attachment_service = _FakeAttachmentService(
            attachment_factory=lambda **kw: _make_attachment(
                should_include_context=True
            )
        )
        adapter = _build_adapter(attachment_service=attachment_service)
        return await adapter.build_prompt_segment(
            reading_record_id=_RECORD_ID,
            user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
            query_text="hello",
        )

    a = await _run_once()
    b = await _run_once()
    assert a.prompt_text == b.prompt_text
    assert a.citations == b.citations
    assert a.context_ids == b.context_ids
    assert a.source_pack_hash == b.source_pack_hash
    assert a.query_sha256 == b.query_sha256
    assert a.status == b.status
    assert a.metadata_json == b.metadata_json
    assert a.include_in_prompt == b.include_in_prompt
    assert a.kind == b.kind


# ---------------------------------------------------------------------------
# 13. Constants
# ---------------------------------------------------------------------------


def test_default_constants() -> None:
    assert DEFAULT_INTEGRATION_LIMIT == 8
    assert DEFAULT_INTEGRATION_MAX_CONTEXT_CHARS == 4000


def test_segment_kind_constant() -> None:
    assert SEGMENT_KIND == "article_rag_context"


def test_failure_code_constant() -> None:
    assert (
        FAILURE_CODE_INTEGRATION_UNEXPECTED_ERROR
        == "article_rag_ask_integration_unexpected_error"
    )


# ---------------------------------------------------------------------------
# 14. Reviewer fixes: repr=False, shape validation, value-level guard
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_prompt_text_with_secret_does_not_leak_in_repr_or_str() -> None:
    """Reviewer P1a fix: even when ``prompt_text`` carries the
    secret query (because the I4G composer output is allowed
    to embed query fragments), the default ``repr(segment)``
    and ``str(segment)`` MUST NOT echo it.  The field itself
    is still accessible (the Ask runtime reads it directly);
    only the default debug surface is scrubbed.

    Without ``field(repr=False)`` on the user-content fields,
    Python's default dataclass repr would print ``prompt_text``
    / ``citations`` / ``metadata_json`` in full, leaking
    chunk text / query fragments into logs.
    """
    secret = "SECRET-QUERY-DO-NOT-LEAK-DO-NOT-LEAK"
    attachment_service = _FakeAttachmentService(
        attachment_factory=lambda **kw: _make_attachment(
            should_include_context=True,
            prompt_context_text=f"[rag-1] score=0.9\n{secret}",
        )
    )
    adapter = _build_adapter(attachment_service=attachment_service)
    segment = await adapter.build_prompt_segment(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text=secret,
    )
    # The field itself is unchanged — Ask runtime reads it.
    assert secret in segment.prompt_text
    # But the default repr / str MUST NOT echo it.
    assert secret not in repr(segment)
    assert secret not in str(segment)


@pytest.mark.anyio
async def test_repr_does_not_include_citations_or_metadata_json() -> None:
    """Reviewer P1a fix: ``field(repr=False)`` covers
    ``citations`` and ``metadata_json`` too — both are
    user-content-bearing fields and MUST NOT appear in the
    default repr.  The field names MAY appear (e.g. as
    ``citations=...`` would normally expand the dict), but
    with ``repr=False`` they are suppressed.
    """
    secret_citation_value = "SECRET-IN-CITATION-DO-NOT-LEAK"
    citation = {
        "context_id": "rag-1",
        "chunk_id": "c1",
        "citation": {
            "reading_record_id": str(_RECORD_ID),
            "stable_document_id": str(_STABLE_DOC_ID),
            "base_id": str(_BASE_ID),
            "record_generation": 1,
            "block_ids": [secret_citation_value],
            "unit_ids": [],
            "anchor_segment_ids": [],
            "canonical_text_start_utf16": 0,
            "canonical_text_end_utf16": 10,
        },
    }
    attachment_service = _FakeAttachmentService(
        attachment_factory=lambda **kw: _make_attachment(
            should_include_context=True,
            citations=(citation,),
        )
    )
    adapter = _build_adapter(attachment_service=attachment_service)
    segment = await adapter.build_prompt_segment(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
    # The secret IS in the field (legitimate).
    assert any(
        secret_citation_value in c["citation"]["block_ids"]
        for c in segment.citations
    )
    # But the default repr MUST NOT echo it.
    assert secret_citation_value not in repr(segment)
    assert secret_citation_value not in str(segment)


@pytest.mark.anyio
async def test_include_path_shape_validation_fails_soft_on_status_mismatch() -> (
    None
):
    """Reviewer P1b fix: a regression / hostile fake in I4I
    could surface ``should_include_context=True`` with
    ``status="disabled"``.  The adapter MUST NOT trust
    ``should_include_context`` alone — it additionally checks
    that the shape matches the include-path contract.
    """

    class _HostileIncludeAttachment:
        # Mimics a regression: should_include_context=True but
        # status="disabled" (contradictory).
        should_include_context = True
        status = "disabled"
        enabled = True
        fallback_allowed = True
        retryable = False
        query_sha256 = None
        prompt_context_text = "[rag-1] rank=1 score=0.9\nalpha"
        citations = ()
        context_ids = ()
        source_pack_hash = None
        failure_code = "disabled_by_user"
        omitted_hit_count = 0
        budget_exceeded = False
        reading_record_id = _RECORD_ID
        stable_document_id = _STABLE_DOC_ID
        base_id = _BASE_ID
        record_generation = 1
        plan_content_sha256 = _PLAN_HASH

    class _ReturningAttachmentService:
        async def build_for_ask(self, **kwargs: Any) -> Any:
            return _HostileIncludeAttachment()

    adapter = ArticleRagAskIntegrationAdapter(
        attachment_service=_ReturningAttachmentService()
    )
    segment = await adapter.build_prompt_segment(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
    # Shape mismatch → fail-soft.
    assert segment.status == "not_indexed_or_unavailable"
    assert segment.include_in_prompt is False
    assert segment.failure_code == FAILURE_CODE_INTEGRATION_UNEXPECTED_ERROR
    assert segment.prompt_text == ""
    assert segment.citations == ()


@pytest.mark.anyio
async def test_include_path_shape_validation_fails_soft_on_empty_prompt() -> (
    None
):
    """Reviewer P1b fix: ``should_include_context=True`` with
    empty ``prompt_context_text`` is a shape mismatch — fail
    soft.
    """

    class _EmptyPromptIncludeAttachment:
        should_include_context = True
        status = "available"
        enabled = True
        fallback_allowed = True
        retryable = False
        query_sha256 = hashlib.sha256(b"x").hexdigest()
        prompt_context_text = ""  # empty!
        citations = ()
        context_ids = ()
        source_pack_hash = None
        failure_code = None
        omitted_hit_count = 0
        budget_exceeded = False
        reading_record_id = _RECORD_ID
        stable_document_id = _STABLE_DOC_ID
        base_id = _BASE_ID
        record_generation = 1
        plan_content_sha256 = _PLAN_HASH

    class _ReturningAttachmentService:
        async def build_for_ask(self, **kwargs: Any) -> Any:
            return _EmptyPromptIncludeAttachment()

    adapter = ArticleRagAskIntegrationAdapter(
        attachment_service=_ReturningAttachmentService()
    )
    segment = await adapter.build_prompt_segment(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
    assert segment.status == "not_indexed_or_unavailable"
    assert segment.include_in_prompt is False


@pytest.mark.anyio
async def test_metadata_json_drops_value_with_secret_substring() -> None:
    """Reviewer P2 fix: a regression in the upstream chain
    could put a secret value on an allowlisted key (e.g.
    ``failure_code="SECRET-..."`` or
    ``source_pack_hash="token=ABC"``).  The value-level guard
    MUST drop such entries — the secret MUST NOT appear in
    the segment's metadata_json.
    """
    secret = "SECRET-VALUE-LEVEL-DO-NOT-LEAK"
    attachment_service = _FakeAttachmentService(
        attachment_factory=lambda **kw: _make_attachment(
            should_include_context=True,
            # Overwrite the source_pack_hash with a value
            # containing a forbidden substring.
            source_pack_hash=f"token={secret}",
        )
    )
    adapter = _build_adapter(attachment_service=attachment_service)
    segment = await adapter.build_prompt_segment(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
    # The key is dropped (value guard rejected the substring).
    assert "source_pack_hash" not in segment.metadata_json
    # The secret MUST NOT appear in metadata_json.
    assert secret not in repr(segment.metadata_json)
    for value in segment.metadata_json.values():
        if isinstance(value, str):
            assert secret not in value


@pytest.mark.anyio
async def test_metadata_json_drops_failure_code_with_secret() -> None:
    """Reviewer P2 fix: a regressed ``failure_code`` carrying
    a secret value (e.g. from a hostile fake or a poorly
    configured upstream) MUST be dropped, not surfaced on the
    segment.

    The test forces the include path so ``failure_code`` is
    propagated; a value with a forbidden substring MUST be
    dropped.
    """
    secret = "SECRET-FAILURE-CODE-DO-NOT-LEAK"
    attachment_service = _FakeAttachmentService(
        attachment_factory=lambda **kw: _make_attachment(
            should_include_context=True,
            failure_code=f"api_key={secret}",
        )
    )
    adapter = _build_adapter(attachment_service=attachment_service)
    segment = await adapter.build_prompt_segment(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
    assert "failure_code" not in segment.metadata_json
    assert secret not in repr(segment.metadata_json)
    for value in segment.metadata_json.values():
        if isinstance(value, str):
            assert secret not in value


@pytest.mark.anyio
async def test_metadata_json_drops_value_with_forbidden_substrings() -> None:
    """Reviewer P2 fix: the value guard rejects any of the
    forbidden substrings (``token=``, ``secret``, ``api_key``,
    ``query=``, ``query_text``, ``plate``, ``markdown``, etc.).
    """
    forbidden_values = [
        "token=ABC",
        "SECRET-anything",
        "api_key=XYZ",
        "query_text=leak",
        "plate=opaque",
        "markdown=**leak**",
        "dom=div",
        "slate=path",
        "render_profile=v1",
        "html=<p>",
        "innerHTML=x",
        "password=leak",
        "credential=token",
        "auth=Bearer abc",
    ]
    for forbidden in forbidden_values:
        secret_for_test = forbidden
        attachment_service = _FakeAttachmentService(
            attachment_factory=lambda **kw: _make_attachment(
                should_include_context=True,
                source_pack_hash=secret_for_test,
            )
        )
        adapter = _build_adapter(attachment_service=attachment_service)
        segment = await adapter.build_prompt_segment(
            reading_record_id=_RECORD_ID,
            user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
            query_text="hello",
        )
        assert "source_pack_hash" not in segment.metadata_json, (
            f"forbidden substring {forbidden!r} should drop "
            f"source_pack_hash"
        )
        assert forbidden not in repr(segment.metadata_json)


@pytest.mark.anyio
async def test_metadata_json_drops_overly_long_value() -> None:
    """Reviewer P2 fix: a long ``status`` / ``source_pack_hash``
    value is almost certainly a regression.  Drop it.
    """
    long_value = "x" * 1024
    attachment_service = _FakeAttachmentService(
        attachment_factory=lambda **kw: _make_attachment(
            should_include_context=True,
            source_pack_hash=long_value,
        )
    )
    adapter = _build_adapter(attachment_service=attachment_service)
    segment = await adapter.build_prompt_segment(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
    assert "source_pack_hash" not in segment.metadata_json


@pytest.mark.anyio
async def test_metadata_json_drops_non_scalar_value() -> None:
    """Reviewer P2 fix: the value guard rejects non-scalar
    values (list / dict / tuple).  A regression that puts a
    list on a metadata key MUST be dropped, not echoed.
    """
    attachment_service = _FakeAttachmentService(
        # The attachment dataclass does NOT actually expose a
        # way to put a list on ``source_pack_hash``; we
        # simulate the regression via a hostile fake that
        # returns a non-typed object.
    )

    class _NonScalarMetadataAttachment:
        should_include_context = True
        status = "available"
        enabled = True
        fallback_allowed = True
        retryable = False
        query_sha256 = hashlib.sha256(b"x").hexdigest()
        prompt_context_text = "alpha"
        citations = ()
        context_ids = ()
        # Non-scalar value — the value guard MUST drop it.
        source_pack_hash = ["list", "of", "values"]
        failure_code = None
        omitted_hit_count = 0
        budget_exceeded = False
        reading_record_id = _RECORD_ID
        stable_document_id = _STABLE_DOC_ID
        base_id = _BASE_ID
        record_generation = 1
        plan_content_sha256 = _PLAN_HASH

    class _ReturningAttachmentService:
        async def build_for_ask(self, **kwargs: Any) -> Any:
            return _NonScalarMetadataAttachment()

    adapter = ArticleRagAskIntegrationAdapter(
        attachment_service=_ReturningAttachmentService()
    )
    segment = await adapter.build_prompt_segment(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
    assert "source_pack_hash" not in segment.metadata_json
    # The default repr MUST NOT echo the list contents.
    assert "list" not in repr(segment)
    assert "list" not in str(segment)


# ---------------------------------------------------------------------------
# 15. Reviewer P1 follow-up: top-level field value guard
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_top_level_source_pack_hash_with_secret_is_dropped() -> None:
    """Reviewer P1 follow-up: a regression in the upstream chain
    could put a secret-bearing value on the top-level
    ``source_pack_hash`` field.  The value guard MUST drop it
    (set the field to ``None``) so the secret does not appear
    in ``repr(segment)`` / ``str(segment)`` / the top-level
    field.
    """
    secret = "SECRET-TOKEN-DO-NOT-LEAK"
    attachment_service = _FakeAttachmentService(
        attachment_factory=lambda **kw: _make_attachment(
            should_include_context=True,
            source_pack_hash=f"token={secret}",
        )
    )
    adapter = _build_adapter(attachment_service=attachment_service)
    segment = await adapter.build_prompt_segment(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
    # The top-level field is dropped.
    assert segment.source_pack_hash is None
    # The secret MUST NOT appear in repr / str.
    assert secret not in repr(segment)
    assert secret not in str(segment)
    # The metadata_json version is also dropped (defence in
    # depth).
    assert "source_pack_hash" not in segment.metadata_json


@pytest.mark.anyio
async def test_top_level_failure_code_with_secret_is_dropped() -> None:
    """Reviewer P1 follow-up: a regressed ``failure_code`` on
    the include path (where ``should_include_context=True``)
    carrying a secret value MUST be dropped.
    """
    secret = "SECRET-FAILURE-CODE-DO-NOT-LEAK"
    attachment_service = _FakeAttachmentService(
        attachment_factory=lambda **kw: _make_attachment(
            should_include_context=True,
            failure_code=f"api_key={secret}",
        )
    )
    adapter = _build_adapter(attachment_service=attachment_service)
    segment = await adapter.build_prompt_segment(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
    # The include-path normally has failure_code=None; here
    # the attachment carried a hostile value, so the guard
    # drops it (sets to None).
    assert segment.failure_code is None
    # The secret MUST NOT appear anywhere on the segment.
    assert secret not in repr(segment)
    assert secret not in str(segment)
    assert "failure_code" not in segment.metadata_json


@pytest.mark.anyio
async def test_top_level_failure_code_with_secret_dropped_on_no_context() -> None:
    """Reviewer P1 follow-up: the no-context path also
    scrubs the top-level ``failure_code`` (and
    ``source_pack_hash``) — a hostile fake that puts a
    secret on either field MUST NOT leak, even when the
    attachment is well-formed but ``should_include_context=False``.
    """
    secret = "SECRET-NO-CONTEXT-DO-NOT-LEAK"
    attachment_service = _FakeAttachmentService(
        attachment_factory=lambda **kw: _make_attachment(
            should_include_context=False,
            status="not_indexed_or_unavailable",
            failure_code=f"api_key={secret}",
            prompt_context_text="",
            citations=(),
            context_ids=(),
            source_pack_hash=f"token={secret}",
        )
    )
    adapter = _build_adapter(attachment_service=attachment_service)
    segment = await adapter.build_prompt_segment(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
    # Both top-level fields are dropped.
    assert segment.failure_code is None
    assert segment.source_pack_hash is None
    # The secret MUST NOT appear in repr / str / metadata_json.
    assert secret not in repr(segment)
    assert secret not in str(segment)
    assert "failure_code" not in segment.metadata_json
    assert "source_pack_hash" not in segment.metadata_json


@pytest.mark.anyio
async def test_top_level_safe_string_value_passes_through() -> None:
    """Positive control: a normal (non-hostile) string value
    on ``failure_code`` / ``source_pack_hash`` passes through
    the guard unchanged.
    """
    safe_failure = "context_no_indexed_run"
    safe_hash = "abc123" + "f" * 58  # 64-char hex hash
    attachment_service = _FakeAttachmentService(
        attachment_factory=lambda **kw: _make_attachment(
            should_include_context=False,
            status="not_indexed_or_unavailable",
            failure_code=safe_failure,
            prompt_context_text="",
            citations=(),
            context_ids=(),
            source_pack_hash=safe_hash,
        )
    )
    adapter = _build_adapter(attachment_service=attachment_service)
    segment = await adapter.build_prompt_segment(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
    assert segment.failure_code == safe_failure
    assert segment.source_pack_hash == safe_hash
    # Both surface in metadata_json too (allowlist + safe value).
    assert segment.metadata_json["failure_code"] == safe_failure
    assert segment.metadata_json["source_pack_hash"] == safe_hash


@pytest.mark.anyio
async def test_top_level_overly_long_value_dropped() -> None:
    """A regression that surfaces an over-cap string on
    ``source_pack_hash`` is dropped from the top-level field
    (not just metadata_json).
    """
    long_value = "x" * 1024
    attachment_service = _FakeAttachmentService(
        attachment_factory=lambda **kw: _make_attachment(
            should_include_context=True,
            source_pack_hash=long_value,
        )
    )
    adapter = _build_adapter(attachment_service=attachment_service)
    segment = await adapter.build_prompt_segment(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
    assert segment.source_pack_hash is None
    # And the long value MUST NOT appear in repr.
    assert "x" * 64 not in repr(segment)


# ---------------------------------------------------------------------------
# 16. Reviewer P1 follow-up: attachment status runtime guard
# ---------------------------------------------------------------------------


def _make_attachment_with_status(
    *,
    status: str,
    should_include_context: bool = False,
) -> ArticleRagAskPromptAttachment:
    """Build an ``ArticleRagAskPromptAttachment`` with a
    specific (possibly hostile) status.  All other fields are
    populated with safe defaults so the only thing under test
    is the status guard.
    """
    return ArticleRagAskPromptAttachment(
        enabled=True,
        status=status,
        should_include_context=should_include_context,
        fallback_allowed=True,
        query_sha256=hashlib.sha256(b"x").hexdigest(),
        prompt_context_text="",
        citations=(),
        context_ids=(),
        source_pack_hash=None,
        failure_code=None,
        retryable=False,
        omitted_hit_count=0,
        budget_exceeded=False,
        reading_record_id=_RECORD_ID,
        stable_document_id=_STABLE_DOC_ID,
        base_id=_BASE_ID,
        record_generation=1,
        plan_content_sha256=_PLAN_HASH,
    )


@pytest.mark.anyio
async def test_paused_status_fails_soft_on_no_context_path() -> None:
    """Reviewer P1 follow-up: a regression / hostile fake in
    the I4I attachment service could surface an unrecognised
    status (``"paused"``) on the no-context path.  The
    attachment's runtime status guard fail-softs to
    ``not_indexed_or_unavailable`` so the Ask runtime's
    default branch still works.
    """
    attachment_service = _FakeAttachmentService(
        attachment_factory=lambda **kw: _make_attachment_with_status(
            status="paused", should_include_context=False
        )
    )
    adapter = _build_adapter(attachment_service=attachment_service)
    segment = await adapter.build_prompt_segment(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
    assert segment.status == "not_indexed_or_unavailable"
    assert segment.include_in_prompt is False
    assert segment.failure_code == FAILURE_CODE_INTEGRATION_UNEXPECTED_ERROR
    # The hostile status string MUST NOT appear in repr / str.
    assert "paused" not in repr(segment)
    assert "paused" not in str(segment)


@pytest.mark.anyio
async def test_empty_string_status_fails_soft_on_no_context_path() -> None:
    """An empty-string status is a contract violation — fail
    soft.
    """
    attachment_service = _FakeAttachmentService(
        attachment_factory=lambda **kw: _make_attachment_with_status(
            status="", should_include_context=False
        )
    )
    adapter = _build_adapter(attachment_service=attachment_service)
    segment = await adapter.build_prompt_segment(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
    assert segment.status == "not_indexed_or_unavailable"
    assert segment.include_in_prompt is False


@pytest.mark.anyio
async def test_secret_bearing_status_does_not_leak_in_repr() -> None:
    """A regression / hostile fake could put a secret value on
    the status field.  The runtime guard fail-softs to
    ``not_indexed_or_unavailable`` AND the segment's status
    field is ``field(repr=False)`` so even if the status
    string slipped through, it would not appear in
    ``repr(segment)`` / ``str(segment)``.
    """
    secret = "SECRET-STATUS-DO-NOT-LEAK"
    attachment_service = _FakeAttachmentService(
        attachment_factory=lambda **kw: _make_attachment_with_status(
            status=secret, should_include_context=False
        )
    )
    adapter = _build_adapter(attachment_service=attachment_service)
    segment = await adapter.build_prompt_segment(
        reading_record_id=_RECORD_ID,
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query_text="hello",
    )
    # The segment is fail-soft.
    assert segment.status == "not_indexed_or_unavailable"
    assert segment.include_in_prompt is False
    assert segment.failure_code == FAILURE_CODE_INTEGRATION_UNEXPECTED_ERROR
    # The hostile status string MUST NOT appear ANYWHERE on
    # the segment.
    assert secret not in repr(segment)
    assert secret not in str(segment)
    # It MUST NOT surface in metadata_json either.
    assert secret not in repr(segment.metadata_json)


@pytest.mark.anyio
async def test_all_five_allowed_statuses_round_trip_on_no_context() -> None:
    """Positive control: all 5 I4H status values pass the
    runtime guard unchanged on the no-context path.
    """
    for status in (
        "available",
        "empty",
        "not_indexed_or_unavailable",
        "composer_rejected",
        "disabled",
    ):
        status_for_test = status
        attachment_service = _FakeAttachmentService(
            attachment_factory=lambda **kw: _make_attachment_with_status(
                status=status_for_test, should_include_context=False
            )
        )
        adapter = _build_adapter(attachment_service=attachment_service)
        segment = await adapter.build_prompt_segment(
            reading_record_id=_RECORD_ID,
            user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
            query_text="hello",
        )
        assert segment.status == status
        assert segment.fallback_allowed is True
        assert segment.include_in_prompt is False
        assert segment.prompt_text == ""