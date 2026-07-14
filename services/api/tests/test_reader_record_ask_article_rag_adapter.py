"""Adapter-layer tests for RetrievalBackedArticleRagPort truth boundaries."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.services.reader_record_ask.article_rag_adapter import (
    RetrievalBackedArticleRagPort,
    _hit_from_retrieval,
)

_USER = UUID("11111111-1111-1111-1111-111111111111")
_RECORD = UUID("22222222-2222-2222-2222-222222222222")
_BASE = UUID("33333333-3333-3333-3333-333333333333")
_DOC = UUID("44444444-4444-4444-4444-444444444444")
_RUN = UUID("55555555-5555-5555-5555-555555555555")
_PLAN = "c" * 64
_CHUNK_HASH = "d" * 64


@dataclass
class _FakeHit:
    chunk_id: str
    text: str
    citation: dict[str, Any]
    metadata_json: dict[str, Any]
    score: float = 0.9
    content_sha256: str | None = _CHUNK_HASH


@dataclass
class _FakeResult:
    reading_record_id: UUID = _RECORD
    stable_document_id: UUID = _DOC
    base_id: UUID = _BASE
    record_generation: int = 1
    index_version: str = "article_rag_index_v1"
    plan_content_sha256: str = _PLAN
    index_run_id: UUID | None = _RUN
    hits: tuple[Any, ...] = ()
    provider_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class _FakeRetrieval:
    result: _FakeResult | None = None
    raise_exc: Exception | None = None
    call_count: int = 0

    async def retrieve_for_record(self, **kwargs: Any) -> _FakeResult:
        del kwargs
        self.call_count += 1
        if self.raise_exc is not None:
            raise self.raise_exc
        assert self.result is not None
        return self.result


def _citation(**overrides: object) -> dict[str, Any]:
    payload = {
        "reading_record_id": str(_RECORD),
        "stable_document_id": str(_DOC),
        "base_id": str(_BASE),
        "record_generation": 1,
        "block_ids": ["b1"],
        "unit_ids": ["u1"],
        "anchor_segment_ids": ["s1"],
        "canonical_text_start_utf16": 0,
        "canonical_text_end_utf16": 10,
    }
    payload.update(overrides)
    return payload


def _good_hit(**overrides: object) -> _FakeHit:
    kwargs: dict[str, Any] = dict(
        chunk_id="chunk-1",
        text="eligible text",
        citation=_citation(),
        metadata_json={
            "source_scope": "main_reading_text",
            "block_type": "paragraph",
        },
        content_sha256=_CHUNK_HASH,
    )
    kwargs.update(overrides)
    return _FakeHit(**kwargs)


@pytest.mark.asyncio
async def test_adapter_refuses_search_without_stable_document_id() -> None:
    port = RetrievalBackedArticleRagPort(retrieval=_FakeRetrieval(result=_FakeResult()))
    outcome = await port.search_current_article(
        user_id=_USER,
        reading_record_id=_RECORD,
        base_id=_BASE,
        record_generation=1,
        stable_document_id=None,
        query="q",
        limit=5,
    )
    assert outcome.status == "not_ready"
    assert outcome.detail_code == "stable_document_id_missing"


@pytest.mark.asyncio
async def test_adapter_uses_retrieval_index_run_id_not_secondary_loader() -> None:
    hit = _good_hit()
    retrieval = _FakeRetrieval(
        result=_FakeResult(hits=(hit,), index_run_id=_RUN)
    )
    port = RetrievalBackedArticleRagPort(retrieval=retrieval)
    outcome = await port.search_current_article(
        user_id=_USER,
        reading_record_id=_RECORD,
        base_id=_BASE,
        record_generation=1,
        stable_document_id=_DOC,
        query="q",
        limit=5,
    )
    assert outcome.status == "ok"
    assert outcome.rag_substrate_id == _RUN
    assert retrieval.call_count == 1


@pytest.mark.asyncio
async def test_adapter_missing_index_run_id_unavailable() -> None:
    retrieval = _FakeRetrieval(
        result=_FakeResult(hits=(_good_hit(),), index_run_id=None)
    )
    port = RetrievalBackedArticleRagPort(retrieval=retrieval)
    outcome = await port.search_current_article(
        user_id=_USER,
        reading_record_id=_RECORD,
        base_id=_BASE,
        record_generation=1,
        stable_document_id=_DOC,
        query="q",
        limit=5,
    )
    assert outcome.status == "unavailable"
    assert outcome.detail_code == "missing_index_run_id"
    assert outcome.hits == ()


@pytest.mark.asyncio
async def test_adapter_missing_plan_content_sha256_unavailable() -> None:
    retrieval = _FakeRetrieval(
        result=_FakeResult(hits=(_good_hit(),), plan_content_sha256="")
    )
    port = RetrievalBackedArticleRagPort(retrieval=retrieval)
    outcome = await port.search_current_article(
        user_id=_USER,
        reading_record_id=_RECORD,
        base_id=_BASE,
        record_generation=1,
        stable_document_id=_DOC,
        query="q",
        limit=5,
    )
    assert outcome.status == "unavailable"
    assert outcome.detail_code == "missing_plan_content_sha256"


@pytest.mark.asyncio
async def test_adapter_rejects_hit_without_canonical_range() -> None:
    hit = _good_hit(
        citation=_citation(
            canonical_text_start_utf16=None,
            canonical_text_end_utf16=None,
        )
    )
    port = RetrievalBackedArticleRagPort(
        retrieval=_FakeRetrieval(result=_FakeResult(hits=(hit,)))
    )
    outcome = await port.search_current_article(
        user_id=_USER,
        reading_record_id=_RECORD,
        base_id=_BASE,
        record_generation=1,
        stable_document_id=_DOC,
        query="q",
        limit=5,
    )
    assert outcome.status == "empty"
    assert outcome.detail_code == "no_eligible_hits"
    assert outcome.rag_substrate_id == _RUN


@pytest.mark.asyncio
async def test_adapter_rejects_hit_without_plan_content_hash() -> None:
    hit = _good_hit(content_sha256=None)
    # Also ensure metadata does not smuggle a derived hash path.
    hit.metadata_json = {
        "source_scope": "main_reading_text",
        "block_type": "paragraph",
    }
    port = RetrievalBackedArticleRagPort(
        retrieval=_FakeRetrieval(result=_FakeResult(hits=(hit,)))
    )
    outcome = await port.search_current_article(
        user_id=_USER,
        reading_record_id=_RECORD,
        base_id=_BASE,
        record_generation=1,
        stable_document_id=_DOC,
        query="q",
        limit=5,
    )
    assert outcome.status == "empty"
    assert outcome.hits == ()


@pytest.mark.asyncio
async def test_adapter_rejects_hit_with_non_hex_content_hash() -> None:
    hit = _good_hit(content_sha256="not-a-real-sha")
    port = RetrievalBackedArticleRagPort(
        retrieval=_FakeRetrieval(result=_FakeResult(hits=(hit,)))
    )
    outcome = await port.search_current_article(
        user_id=_USER,
        reading_record_id=_RECORD,
        base_id=_BASE,
        record_generation=1,
        stable_document_id=_DOC,
        query="q",
        limit=5,
    )
    assert outcome.status == "empty"


@pytest.mark.asyncio
async def test_adapter_rejects_wrong_record_base_generation_on_hit() -> None:
    hit = _good_hit(
        citation=_citation(base_id=str(uuid4()), record_generation=9)
    )
    port = RetrievalBackedArticleRagPort(
        retrieval=_FakeRetrieval(result=_FakeResult(hits=(hit,)))
    )
    outcome = await port.search_current_article(
        user_id=_USER,
        reading_record_id=_RECORD,
        base_id=_BASE,
        record_generation=1,
        stable_document_id=_DOC,
        query="q",
        limit=5,
    )
    assert outcome.status == "empty"


@pytest.mark.asyncio
async def test_adapter_rejects_result_level_identity_mismatch() -> None:
    """Retrieval returned a different base than the envelope asked for."""
    port = RetrievalBackedArticleRagPort(
        retrieval=_FakeRetrieval(
            result=_FakeResult(
                base_id=uuid4(),
                hits=(_good_hit(),),
            )
        )
    )
    outcome = await port.search_current_article(
        user_id=_USER,
        reading_record_id=_RECORD,
        base_id=_BASE,
        record_generation=1,
        stable_document_id=_DOC,
        query="q",
        limit=5,
    )
    assert outcome.status == "unavailable"
    assert outcome.detail_code == "identity_mismatch"


def test_hit_from_retrieval_never_hashes_text() -> None:
    """Missing plan hash must reject — not invent SHA from text."""
    hit = _FakeHit(
        chunk_id="c1",
        text="some body text that could be hashed",
        citation=_citation(),
        metadata_json={
            "source_scope": "main_reading_text",
            "block_type": "paragraph",
        },
        content_sha256=None,
    )
    view = _hit_from_retrieval(
        hit,
        envelope_record_id=_RECORD,
        envelope_base_id=_BASE,
        envelope_generation=1,
        envelope_stable_document_id=_DOC,
    )
    assert view is None
