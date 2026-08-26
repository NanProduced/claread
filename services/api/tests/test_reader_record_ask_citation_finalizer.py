"""ASK-PROV-canonical finalizer + public citation projection."""

from __future__ import annotations

import json

import pytest

from app.schemas.reader_record_ask_stream import EXECUTION_VERSION_AGENTIC_V2
from app.services.reader_record_ask.answer_block_provenance import (
    AnswerBlockDraft,
    ValidatedAnswerBlocks,
)
from app.services.reader_record_ask.citation_navigation import (
    LiveDocumentFence,
    resolve_citation_navigation,
)
from app.services.reader_record_ask.evidence import (
    ServerEvidenceHandle,
    ServerEvidenceObservation,
    mint_evidence_handle_id,
)
from app.services.reader_record_ask.evidence_registry import EvidenceRegistry
from app.services.reader_record_ask.fence import StaticGenerationFence
from app.services.reader_record_ask.finalizer import (
    SOURCE_UNAVAILABLE_ANSWER_TEXT,
    finalize_agent_answer,
)
from app.services.reader_record_ask.production_stream import (
    build_completed_dto,
    build_restricted_evidence_json,
)
from app.services.reader_record_ask.runtime import ReadingRecordAskRunResult


def _fp() -> str:
    return "a" * 64


def _register_article_obs(
    registry: EvidenceRegistry,
    *,
    snippet: str = "snippet text",
    unit_id: str | None = "u1",
    anchor_segment_id: str | None = "s1",
) -> str:
    handle_id = mint_evidence_handle_id()
    handle = ServerEvidenceHandle(
        handle_id=handle_id,
        kind="article_seed",
        source_tool="baseline_context",
        envelope_fingerprint=registry.envelope_fingerprint,
    )
    obs = ServerEvidenceObservation(
        handle=handle,
        snippet=snippet,
        unit_id=unit_id,
        anchor_segment_id=anchor_segment_id,
        rag_citation=None,
    )
    registry.register(obs)
    return handle_id


@pytest.mark.asyncio
async def test_finalizer_article_general_mixed_citation_mapping() -> None:
    fp = _fp()
    registry = EvidenceRegistry(fp)
    h1 = _register_article_obs(registry, snippet="first")
    h2 = _register_article_obs(registry, snippet="second")

    # Build a minimal envelope-like object with required attributes.
    from types import SimpleNamespace
    from uuid import uuid4

    envelope = SimpleNamespace(
        envelope_fingerprint=fp,
        reading_record_id=uuid4(),
        base_id=uuid4(),
        record_generation=1,
        stable_document_id=None,
    )

    validated = ValidatedAnswerBlocks(
        blocks=(
            AnswerBlockDraft(
                text="Article claim one.",
                basis="article",
                article_scope="evidence_bounded",
                evidence_handles=(h1, h2),
            ),
            AnswerBlockDraft(
                text="General claim.",
                basis="general",
                article_scope=None,
                evidence_handles=(),
            ),
            AnswerBlockDraft(
                text="Article claim two reuses first.",
                basis="article",
                article_scope="evidence_bounded",
                evidence_handles=(h1,),
            ),
        ),
        knowledge_mode="mixed",
    )

    result = await finalize_agent_answer(
        envelope=envelope,  # type: ignore[arg-type]
        registry=registry,
        fence=StaticGenerationFence(live_generation=1),
        response_kind="grounded_answer",
        validated_answer_blocks=validated,
    )

    assert result.status == "ok"
    assert result.knowledge_mode == "mixed"
    assert result.source_status is None
    assert [c.citation_id for c in result.public_citations] == ["c1", "c2"]
    assert result.public_citations[0].snippet == "first"
    assert result.public_citations[1].snippet == "second"
    assert result.answer_blocks[0].citation_ids == ["c1", "c2"]
    assert result.answer_blocks[1].citation_ids == []
    assert result.answer_blocks[2].citation_ids == ["c1"]
    # Internal bindings keep handles; public does not.
    assert all(b.handle_id.startswith("evh_") for b in result.citation_bindings)
    public = {
        "answer_blocks": [b.model_dump(mode="json") for b in result.answer_blocks],
        "citations": [c.model_dump(mode="json") for c in result.public_citations],
    }
    blob = json.dumps(public)
    assert "evh_" not in blob
    assert "handle_id" not in blob


@pytest.mark.asyncio
async def test_finalizer_fabricated_handle_fail_closed() -> None:
    from types import SimpleNamespace
    from uuid import uuid4

    fp = _fp()
    registry = EvidenceRegistry(fp)
    envelope = SimpleNamespace(
        envelope_fingerprint=fp,
        reading_record_id=uuid4(),
        base_id=uuid4(),
        record_generation=1,
        stable_document_id=None,
    )
    fake = "evh_" + "f" * 32
    validated = ValidatedAnswerBlocks(
        blocks=(
            AnswerBlockDraft(
                text="Bad cite.",
                basis="article",
                article_scope="evidence_bounded",
                evidence_handles=(fake,),
            ),
        ),
        knowledge_mode="article_grounded",
    )
    result = await finalize_agent_answer(
        envelope=envelope,  # type: ignore[arg-type]
        registry=registry,
        fence=StaticGenerationFence(live_generation=1),
        response_kind="grounded_answer",
        validated_answer_blocks=validated,
    )
    assert result.status == "invalid_citations"
    assert result.public_citations == ()
    assert result.answer_blocks == ()
    assert fake in result.rejected_handles


@pytest.mark.asyncio
async def test_source_unavailable_host_projection() -> None:
    from types import SimpleNamespace
    from uuid import uuid4

    fp = _fp()
    registry = EvidenceRegistry(fp)
    envelope = SimpleNamespace(
        envelope_fingerprint=fp,
        reading_record_id=uuid4(),
        base_id=uuid4(),
        record_generation=1,
        stable_document_id=None,
    )
    result = await finalize_agent_answer(
        envelope=envelope,  # type: ignore[arg-type]
        registry=registry,
        fence=StaticGenerationFence(live_generation=1),
        response_kind="source_unavailable",
    )
    assert result.status == "ok"
    assert result.answer_text == SOURCE_UNAVAILABLE_ANSWER_TEXT
    assert result.answer_blocks == ()
    assert result.public_citations == ()
    assert result.knowledge_mode is None
    assert result.source_status == "article_source_unavailable"
    assert result.citation_bindings == ()


@pytest.mark.asyncio
async def test_public_completed_dto_no_evh() -> None:
    from types import SimpleNamespace
    from uuid import uuid4

    fp = _fp()
    registry = EvidenceRegistry(fp)
    h1 = _register_article_obs(registry)
    envelope = SimpleNamespace(
        envelope_fingerprint=fp,
        reading_record_id=uuid4(),
        base_id=uuid4(),
        record_generation=1,
        stable_document_id=None,
    )
    validated = ValidatedAnswerBlocks(
        blocks=(
            AnswerBlockDraft(
                text="Only article.",
                basis="article",
                article_scope="evidence_bounded",
                evidence_handles=(h1,),
            ),
        ),
        knowledge_mode="article_grounded",
    )
    finalized = await finalize_agent_answer(
        envelope=envelope,  # type: ignore[arg-type]
        registry=registry,
        fence=StaticGenerationFence(live_generation=1),
        response_kind="grounded_answer",
        validated_answer_blocks=validated,
    )
    run_result = ReadingRecordAskRunResult(
        final_text=finalized.answer_text,
        finalized=finalized,
    )
    completed = build_completed_dto(
        run_result=run_result,
        message_id="msg-1",
        thread_id="thread-1",
        turn_run_id="turn-1",
        envelope=envelope,  # type: ignore[arg-type]
    )
    payload = completed.model_dump(mode="json")
    assert payload["execution_version"] == EXECUTION_VERSION_AGENTIC_V2
    assert payload["knowledge_mode"] == "article_grounded"
    blob = json.dumps(payload)
    for forbidden in (
        "evh_",
        "handle_id",
        "cited_evidence_handles",
        "envelope_fingerprint",
        "rag_navigation",
        "web_snapshot",
        "evidence_scope",
    ):
        assert forbidden not in blob

    restricted = build_restricted_evidence_json(
        run_result=run_result,
        envelope=envelope,  # type: ignore[arg-type]
    )
    assert restricted[0]["citation_id"] == "c1"
    assert restricted[0]["handle_id"].startswith("evh_")


@pytest.mark.asyncio
async def test_finalizer_deletes_internal_handle_echoed_into_block_text() -> None:
    """A model echoing an internal evidence handle INTO block text
    (instead of the structured evidence_handles field) must never leak it
    into the public answer, the persisted completed DTO, or cold recovery.
    The handle is DELETED — never a citation-shaped marker without
    provenance — while the normal c1/c2 citation mapping is untouched."""
    from types import SimpleNamespace
    from uuid import uuid4

    fp = _fp()
    registry = EvidenceRegistry(fp)
    h1 = _register_article_obs(registry, snippet="first")
    envelope = SimpleNamespace(
        envelope_fingerprint=fp,
        reading_record_id=uuid4(),
        base_id=uuid4(),
        record_generation=1,
        stable_document_id=None,
    )
    leaked = "evh_" + "9f" * 16
    validated = ValidatedAnswerBlocks(
        blocks=(
            AnswerBlockDraft(
                text=f"Article claim cites {leaked} inline.",
                basis="article",
                article_scope="evidence_bounded",
                evidence_handles=(h1,),
            ),
            AnswerBlockDraft(
                text="General claim.",
                basis="general",
                article_scope=None,
                evidence_handles=(),
            ),
        ),
        knowledge_mode="mixed",
    )
    result = await finalize_agent_answer(
        envelope=envelope,  # type: ignore[arg-type]
        registry=registry,
        fence=StaticGenerationFence(live_generation=1),
        response_kind="grounded_answer",
        validated_answer_blocks=validated,
    )
    assert result.status == "ok"
    assert leaked not in result.answer_text
    assert result.answer_blocks[0].text == "Article claim cites  inline."
    # No pseudo-citation marker is minted for the unresolved handle.
    assert "〔引用〕" not in result.answer_text
    # Normal public citation mapping ([1][2] → c1/c2) is untouched.
    assert result.answer_blocks[0].citation_ids == ["c1"]
    assert result.answer_blocks[1].citation_ids == []
    assert result.public_citations[0].citation_id == "c1"
    # Internal bindings still carry the real handles for audit.
    assert result.citation_bindings[0].handle_id == h1
    blob = json.dumps(
        [b.model_dump(mode="json") for b in result.answer_blocks], ensure_ascii=False
    )
    assert "evh_" not in blob

    # The public completed DTO (SSE + persistence + cold recovery surface)
    # inherits the redacted text.
    run_result = ReadingRecordAskRunResult(
        final_text=result.answer_text,
        finalized=result,
    )
    completed = build_completed_dto(
        run_result=run_result,
        message_id="msg-1",
        thread_id="thread-1",
        turn_run_id="turn-1",
        envelope=envelope,  # type: ignore[arg-type]
    )
    completed_blob = json.dumps(completed.model_dump(mode="json"), ensure_ascii=False)
    assert leaked not in completed_blob
    assert "〔引用〕" not in completed_blob
    assert completed.answer_text == "Article claim cites  inline.\n\nGeneral claim."


@pytest.mark.asyncio
async def test_finalizer_deletes_internal_handle_from_clarification_text() -> None:
    from types import SimpleNamespace
    from uuid import uuid4

    fp = _fp()
    registry = EvidenceRegistry(fp)
    envelope = SimpleNamespace(
        envelope_fingerprint=fp,
        reading_record_id=uuid4(),
        base_id=uuid4(),
        record_generation=1,
        stable_document_id=None,
    )
    leaked = "evh_" + "9f" * 16
    result = await finalize_agent_answer(
        envelope=envelope,  # type: ignore[arg-type]
        registry=registry,
        fence=StaticGenerationFence(live_generation=1),
        response_kind="clarification",
        clarification_text=f"需要确认引用 {leaked} 吗？",
    )
    assert result.status == "ok"
    assert leaked not in result.answer_text
    assert "〔引用〕" not in result.answer_text
    assert result.answer_blocks[0].text == "需要确认引用  吗？"


@pytest.mark.asyncio
async def test_finalizer_handle_only_block_text_fails_closed() -> None:
    """A block whose entire text is internal handles must not complete:
    no empty public block, no pseudo-citation output."""
    from types import SimpleNamespace
    from uuid import uuid4

    fp = _fp()
    registry = EvidenceRegistry(fp)
    envelope = SimpleNamespace(
        envelope_fingerprint=fp,
        reading_record_id=uuid4(),
        base_id=uuid4(),
        record_generation=1,
        stable_document_id=None,
    )
    validated = ValidatedAnswerBlocks(
        blocks=(
            AnswerBlockDraft(
                text="evh_" + "9f" * 16,
                basis="general",
                article_scope=None,
                evidence_handles=(),
            ),
        ),
        knowledge_mode="mixed",
    )
    result = await finalize_agent_answer(
        envelope=envelope,  # type: ignore[arg-type]
        registry=registry,
        fence=StaticGenerationFence(live_generation=1),
        response_kind="grounded_answer",
        validated_answer_blocks=validated,
    )
    assert result.status == "invalid_citations"
    assert result.answer_blocks == ()
    assert result.public_citations == ()


@pytest.mark.parametrize(
    ("hex_len", "kept"),
    [
        (8, True),  # evh_deadbeef — ordinary literal, preserved
        (31, True),
        (33, True),
        (64, True),
        (32, False),  # exact canonical mint shape → deleted
    ],
)
def test_finalizer_mint_shape_precision(hex_len: int, kept: bool) -> None:
    """Only the canonical mint shape (evh_ + exactly 32 lowercase hex,
    per evidence.py) is gated; other runs are ordinary text."""
    from app.services.reader_record_ask.finalizer import (
        redact_public_answer_text,
    )

    run = "evh_" + "de" * (hex_len // 2) + ("f" if hex_len % 2 else "")
    text = f"value {run} here"
    out = redact_public_answer_text(text)
    if kept:
        assert out == text
    else:
        assert run not in out
        assert out == "value  here"


def test_finalizer_uppercase_hex_is_not_a_mint_handle() -> None:
    from app.services.reader_record_ask.finalizer import (
        redact_public_answer_text,
    )

    run = "evh_" + "DEADBEEF" * 4
    text = f"value {run} here"
    assert redact_public_answer_text(text) == text


@pytest.mark.parametrize(
    "literal",
    [
        "evh_" + "a" * 32 + "A",
        "evh_" + "a" * 32 + "g",
        "evh_" + "a" * 32 + "_",
        "xevh_" + "a" * 32,
        "_evh_" + "a" * 32,
    ],
)
def test_finalizer_preserves_noncanonical_identifier_boundaries(literal: str) -> None:
    """A canonical-looking substring inside a larger identifier is ordinary text."""
    from app.services.reader_record_ask.finalizer import (
        redact_public_answer_text,
    )

    text = f"value {literal} here"
    assert redact_public_answer_text(text) == text


def test_citation_navigation_fence() -> None:
    restricted = [
        {
            "citation_id": "c1",
            "handle_id": "evh_" + "a" * 32,
            "unit_id": "u1",
            "anchor_segment_id": "s1",
            "evidence_scope": {
                "reading_record_id": "rec-1",
                "base_id": "base-1",
                "record_generation": 2,
                "stable_document_id": "doc-1",
            },
            "rag_citation": {
                "stable_document_id": "doc-1",
                "base_id": "base-1",
                "record_generation": 2,
                "unit_ids": ["u1"],
                "anchor_segment_ids": ["s1"],
                "canonical_text_start_utf16": 0,
                "canonical_text_end_utf16": 10,
            },
        }
    ]
    ok = resolve_citation_navigation(
        citation_id="c1",
        restricted_evidence=restricted,
        live_fence=LiveDocumentFence(
            reading_record_id="rec-1",
            base_id="base-1",
            record_generation=2,
            stable_document_id="doc-1",
        ),
    )
    assert ok.status == "ok"
    assert ok.location is not None
    assert ok.location.unit_id == "u1"
    assert ok.location.anchor_segment_id == "s1"

    stale = resolve_citation_navigation(
        citation_id="c1",
        restricted_evidence=restricted,
        live_fence=LiveDocumentFence(
            reading_record_id="rec-1",
            base_id="base-1",
            record_generation=3,
            stable_document_id="doc-1",
        ),
    )
    assert stale.status == "stale_generation"

    missing = resolve_citation_navigation(
        citation_id="c9",
        restricted_evidence=restricted,
        live_fence=LiveDocumentFence(
            reading_record_id="rec-1",
            base_id="base-1",
            record_generation=2,
            stable_document_id="doc-1",
        ),
    )
    assert missing.status == "not_found"


def test_citation_navigation_live_stable_missing_fail_closed() -> None:
    """Binding claims stable_document_id but live fence has none → unavailable."""
    restricted = [
        {
            "citation_id": "c1",
            "handle_id": "evh_" + "a" * 32,
            "unit_id": "u1",
            "anchor_segment_id": "s1",
            "evidence_scope": {
                "reading_record_id": "rec-1",
                "base_id": "base-1",
                "record_generation": 1,
                "stable_document_id": "doc-1",
            },
        }
    ]
    result = resolve_citation_navigation(
        citation_id="c1",
        restricted_evidence=restricted,
        live_fence=LiveDocumentFence(
            reading_record_id="rec-1",
            base_id="base-1",
            record_generation=1,
            stable_document_id=None,
        ),
    )
    assert result.status == "unavailable"
    assert result.reason == "live_stable_document_missing"
    assert result.location is None


def test_citation_navigation_scope_stable_mismatch() -> None:
    restricted = [
        {
            "citation_id": "c1",
            "handle_id": "evh_" + "a" * 32,
            "unit_id": "u1",
            "evidence_scope": {
                "reading_record_id": "rec-1",
                "base_id": "base-1",
                "record_generation": 1,
                "stable_document_id": "doc-stored",
            },
        }
    ]
    result = resolve_citation_navigation(
        citation_id="c1",
        restricted_evidence=restricted,
        live_fence=LiveDocumentFence(
            reading_record_id="rec-1",
            base_id="base-1",
            record_generation=1,
            stable_document_id="doc-live",
        ),
    )
    assert result.status == "identity_mismatch"
    assert result.reason == "stable_document"


def test_citation_navigation_rag_stable_mismatch() -> None:
    restricted = [
        {
            "citation_id": "c1",
            "handle_id": "evh_" + "a" * 32,
            "unit_id": "u1",
            "evidence_scope": {
                "reading_record_id": "rec-1",
                "base_id": "base-1",
                "record_generation": 1,
                "stable_document_id": None,
            },
            "rag_citation": {
                "stable_document_id": "doc-rag",
                "base_id": "base-1",
                "record_generation": 1,
                "unit_ids": ["u1"],
                "anchor_segment_ids": ["s1"],
                "canonical_text_start_utf16": 0,
                "canonical_text_end_utf16": 5,
            },
        }
    ]
    result = resolve_citation_navigation(
        citation_id="c1",
        restricted_evidence=restricted,
        live_fence=LiveDocumentFence(
            reading_record_id="rec-1",
            base_id="base-1",
            record_generation=1,
            stable_document_id="doc-live",
        ),
    )
    assert result.status == "identity_mismatch"
    assert result.reason == "stable_document"
