"""RAG integration gate — entry-level zero-I/O and single-path contracts.

Covers the production Ask entry for Article RAG:
  * flag off → zero I/O
  * enabled but incomplete providers → zero I/O
  * ready → single retrieve call
  * identity mismatch discards
  * non-ok statuses do not require legacy dual path
  * new path never imports legacy ArticleRagPromptIntegration
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from app.services.reader_orchestration.article_rag_index_worker import (
    UnconfiguredArticleRagEmbeddingProvider,
)
from app.services.reader_orchestration.article_rag_vector_search import (
    UnconfiguredArticleRagVectorSearcher,
)
from app.services.reader_record_ask.article_rag_adapter import (
    RetrievalBackedArticleRagPort,
)
from app.services.reader_record_ask.article_rag_port import (
    ArticleRagHitView,
    ArticleRagSearchOutcome,
    FakeArticleRagSearchPort,
)
from app.services.reader_record_ask.context_envelope import (
    VerifiedEnvelopeInput,
    build_context_envelope,
)
from app.services.reader_record_ask.evidence_registry import EvidenceRegistry
from app.services.reader_record_ask.fence import FenceCheckResult
from app.services.reader_record_ask.production_wiring import (
    article_rag_query_ready,
    build_production_article_rag_port,
)
from app.services.reader_record_ask.search_current_article_executor import (
    execute_search_current_article,
)
from app.services.reader_record_ask.tool_contracts import SearchCurrentArticleToolInput

_USER = UUID("11111111-1111-1111-1111-111111111111")
_RECORD = UUID("22222222-2222-2222-2222-222222222222")
_BASE = UUID("33333333-3333-3333-3333-333333333333")
_DOC = UUID("44444444-4444-4444-4444-444444444444")
_RUN = UUID("55555555-5555-5555-5555-555555555555")
_PLAN = "c" * 64
_CHUNK = "d" * 64
_SHA = "b" * 64

_RR_ASK_ROOT = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "services"
    / "reader_record_ask"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _envelope_stub() -> Any:
    return build_context_envelope(
        VerifiedEnvelopeInput(
            user_id=_USER,
            reading_record_id=_RECORD,
            base_id=_BASE,
            record_generation=1,
            stable_document_id=_DOC,
            base_content_sha256=_SHA,
            product_state="readable_enhancing",
            readiness_state="article_ready",
            initial_anchor=None,
            visible_range=None,
        )
    )


async def _ok_fence(_envelope: Any) -> FenceCheckResult:
    return FenceCheckResult(ok=True, reason=None)


def _registry_for(envelope: Any) -> EvidenceRegistry:
    return EvidenceRegistry(envelope.envelope_fingerprint)


# ---------------------------------------------------------------------------
# A. Factory zero-I/O gates
# ---------------------------------------------------------------------------


def test_article_rag_query_ready_false_when_flag_off() -> None:
    settings = SimpleNamespace(reader_article_rag_enabled=False)
    assert article_rag_query_ready(settings) is False


def test_article_rag_query_ready_false_when_embedding_unconfigured() -> None:
    settings = SimpleNamespace(reader_article_rag_enabled=True)
    assert (
        article_rag_query_ready(
            settings,
            embedding_provider=UnconfiguredArticleRagEmbeddingProvider(),
            vector_searcher=object(),
        )
        is False
    )


def test_article_rag_query_ready_false_when_vector_unconfigured() -> None:
    settings = SimpleNamespace(reader_article_rag_enabled=True)
    assert (
        article_rag_query_ready(
            settings,
            embedding_provider=object(),
            vector_searcher=UnconfiguredArticleRagVectorSearcher(),
        )
        is False
    )


def test_article_rag_query_ready_true_when_both_configured() -> None:
    settings = SimpleNamespace(reader_article_rag_enabled=True)
    assert (
        article_rag_query_ready(
            settings,
            embedding_provider=object(),
            vector_searcher=object(),
        )
        is True
    )


def test_factory_flag_off_zero_provider_construction() -> None:
    settings = SimpleNamespace(reader_article_rag_enabled=False)
    with patch(
        "app.services.reader_orchestration.article_rag_embedding_provider."
        "build_default_article_rag_embedding_provider",
    ) as emb:
        with patch(
            "app.services.reader_orchestration.article_rag_vector_search."
            "build_default_article_rag_vector_searcher",
        ) as vec:
            with patch(
                "app.services.reader_orchestration.article_rag_retrieval_service."
                "ArticleRagRetrievalService",
            ) as ret:
                assert build_production_article_rag_port(settings) is None
    emb.assert_not_called()
    vec.assert_not_called()
    ret.assert_not_called()


def test_factory_enabled_incomplete_providers_zero_retrieval_service() -> None:
    settings = SimpleNamespace(reader_article_rag_enabled=True)
    with patch(
        "app.services.reader_orchestration.article_rag_embedding_provider."
        "build_default_article_rag_embedding_provider",
        return_value=UnconfiguredArticleRagEmbeddingProvider(),
    ) as emb:
        with patch(
            "app.services.reader_orchestration.article_rag_vector_search."
            "build_default_article_rag_vector_searcher",
            return_value=UnconfiguredArticleRagVectorSearcher(),
        ) as vec:
            with patch(
                "app.services.reader_orchestration.article_rag_retrieval_service."
                "ArticleRagRetrievalService",
            ) as ret:
                assert build_production_article_rag_port(settings) is None
    emb.assert_called_once()
    vec.assert_called_once()
    # Critical: no retrieval service → no Postgres plan / index I/O path.
    ret.assert_not_called()


def test_factory_enabled_only_embedding_incomplete_zero_retrieval() -> None:
    settings = SimpleNamespace(reader_article_rag_enabled=True)
    with patch(
        "app.services.reader_orchestration.article_rag_embedding_provider."
        "build_default_article_rag_embedding_provider",
        return_value=UnconfiguredArticleRagEmbeddingProvider(),
    ):
        with patch(
            "app.services.reader_orchestration.article_rag_vector_search."
            "build_default_article_rag_vector_searcher",
            return_value=object(),
        ):
            with patch(
                "app.services.reader_orchestration.article_rag_retrieval_service."
                "ArticleRagRetrievalService",
            ) as ret:
                assert build_production_article_rag_port(settings) is None
    ret.assert_not_called()


def test_factory_enabled_only_vector_incomplete_zero_retrieval() -> None:
    settings = SimpleNamespace(reader_article_rag_enabled=True)
    with patch(
        "app.services.reader_orchestration.article_rag_embedding_provider."
        "build_default_article_rag_embedding_provider",
        return_value=object(),
    ):
        with patch(
            "app.services.reader_orchestration.article_rag_vector_search."
            "build_default_article_rag_vector_searcher",
            return_value=UnconfiguredArticleRagVectorSearcher(),
        ):
            with patch(
                "app.services.reader_orchestration.article_rag_retrieval_service."
                "ArticleRagRetrievalService",
            ) as ret:
                assert build_production_article_rag_port(settings) is None
    ret.assert_not_called()


def test_factory_ready_builds_port_single_retrieval_construction() -> None:
    settings = SimpleNamespace(reader_article_rag_enabled=True)
    pool = object()
    embedding = object()
    searcher = object()
    retrieval = MagicMock()
    with patch(
        "app.services.reader_orchestration.article_rag_embedding_provider."
        "build_default_article_rag_embedding_provider",
        return_value=embedding,
    ):
        with patch(
            "app.services.reader_orchestration.article_rag_vector_search."
            "build_default_article_rag_vector_searcher",
            return_value=searcher,
        ):
            with patch(
                "app.services.reader_orchestration.article_rag_retrieval_service."
                "ArticleRagRetrievalService",
                return_value=retrieval,
            ) as ret:
                port = build_production_article_rag_port(settings, pool=pool)
    assert isinstance(port, RetrievalBackedArticleRagPort)
    ret.assert_called_once_with(
        pool=pool,
        embedding_provider=embedding,
        vector_searcher=searcher,
    )


# ---------------------------------------------------------------------------
# A2. Production lifecycle probe wiring (F2)
# ---------------------------------------------------------------------------


class _FakePoolCtx:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *args: object) -> bool:
        return False


class _FakeLifecyclePool:
    def acquire(self) -> _FakePoolCtx:
        return _FakePoolCtx()


@pytest.mark.asyncio
async def test_production_port_reports_indexing_without_retrieval_io() -> None:
    """F2: the production port must consult the lifecycle probe.

    When the (real) lifecycle service reports ``indexing`` for the record,
    the production-built port must surface typed ``indexing`` and NEVER
    touch retrieval (which would otherwise hit plan loading / embedding /
    vector search).
    """
    settings = SimpleNamespace(reader_article_rag_enabled=True)
    pool = _FakeLifecyclePool()
    retrieval = MagicMock()
    with patch(
        "app.services.reader_orchestration.article_rag_embedding_provider."
        "build_default_article_rag_embedding_provider",
        return_value=object(),
    ):
        with patch(
            "app.services.reader_orchestration.article_rag_vector_search."
            "build_default_article_rag_vector_searcher",
            return_value=object(),
        ):
            with patch(
                "app.services.reader_orchestration.article_rag_retrieval_service."
                "ArticleRagRetrievalService",
                return_value=retrieval,
            ):
                port = build_production_article_rag_port(settings, pool=pool)
    assert isinstance(port, RetrievalBackedArticleRagPort)

    from app.services.reader_orchestration.article_rag_index_lifecycle_service import (
        ArticleRagIndexLifecycleService,
    )

    with patch.object(
        ArticleRagIndexLifecycleService,
        "load_article_rag_index_lifecycle_status",
    ) as load_status:
        load_status.return_value = SimpleNamespace(status="indexing")
        outcome = await port.search_current_article(
            user_id=_USER,
            reading_record_id=_RECORD,
            base_id=_BASE,
            record_generation=1,
            stable_document_id=_DOC,
            query="main idea",
            limit=5,
        )

    assert outcome.status == "indexing"
    # Probe was consulted with the caller's identity.
    assert load_status.call_count == 1
    # Zero retrieval I/O (no plan load / embedding / vector search).
    retrieval.retrieve_for_record.assert_not_called()


@pytest.mark.asyncio
async def test_production_port_indexed_still_reaches_retrieval() -> None:
    """F2 negative guard: ``indexed`` must NOT be short-circuited by the
    probe — retrieval still runs so the probe can never permanently
    block queries."""
    settings = SimpleNamespace(reader_article_rag_enabled=True)
    pool = _FakeLifecyclePool()
    retrieval = MagicMock()
    retrieval.retrieve_for_record = AsyncMock(
        return_value=SimpleNamespace(
            reading_record_id=_RECORD,
            base_id=_BASE,
            record_generation=1,
            stable_document_id=_DOC,
            hits=[
                SimpleNamespace(
                    chunk_id="chunk-1",
                    text="eligible text",
                    source_scope="main_reading_text",
                    block_type="paragraph",
                    content_sha256=_CHUNK,
                    canonical_text_start_utf16=0,
                    canonical_text_end_utf16=10,
                    score=0.9,
                    reading_record_id=_RECORD,
                    stable_document_id=_DOC,
                    base_id=_BASE,
                    record_generation=1,
                )
            ],
            index_run_id=_RUN,
            plan_content_sha256=_PLAN,
        )
    )
    with patch(
        "app.services.reader_orchestration.article_rag_embedding_provider."
        "build_default_article_rag_embedding_provider",
        return_value=object(),
    ):
        with patch(
            "app.services.reader_orchestration.article_rag_vector_search."
            "build_default_article_rag_vector_searcher",
            return_value=object(),
        ):
            with patch(
                "app.services.reader_orchestration.article_rag_retrieval_service."
                "ArticleRagRetrievalService",
                return_value=retrieval,
            ):
                port = build_production_article_rag_port(settings, pool=pool)
    assert isinstance(port, RetrievalBackedArticleRagPort)

    from app.services.reader_orchestration.article_rag_index_lifecycle_service import (
        ArticleRagIndexLifecycleService,
    )

    with patch.object(
        ArticleRagIndexLifecycleService,
        "load_article_rag_index_lifecycle_status",
    ) as load_status:
        load_status.return_value = SimpleNamespace(status="indexed")
        outcome = await port.search_current_article(
            user_id=_USER,
            reading_record_id=_RECORD,
            base_id=_BASE,
            record_generation=1,
            stable_document_id=_DOC,
            query="main idea",
            limit=5,
        )

    # Probe consulted AND retrieval reached — probe did not block.
    assert load_status.call_count == 1
    retrieval.retrieve_for_record.assert_awaited_once()
    assert outcome.status != "indexing"


# ---------------------------------------------------------------------------
# B. Search executor: port None / single call / identity / non-ok
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_executor_port_none_zero_rag_io() -> None:
    envelope = _envelope_stub()
    registry = _registry_for(envelope)
    result, consumed = await execute_search_current_article(
        envelope=envelope,
        tool_input=SearchCurrentArticleToolInput(query="main idea"),
        article_rag=None,
        fence=_ok_fence,
        registry=registry,
        search_calls_so_far=0,
    )
    assert result.status == "unavailable"
    assert result.payloads is not None
    assert result.payloads.get("detail_code") == "rag_port_missing"
    assert consumed is True
    assert result.evidence_handles == []


@pytest.mark.asyncio
async def test_search_executor_ok_single_port_call() -> None:
    envelope = _envelope_stub()
    registry = _registry_for(envelope)
    hit = ArticleRagHitView(
        chunk_id="chunk-1",
        text="eligible text",
        source_scope="main_reading_text",
        block_type="paragraph",
        content_sha256=_CHUNK,
        canonical_text_start_utf16=0,
        canonical_text_end_utf16=10,
        score=0.9,
        reading_record_id=_RECORD,
        stable_document_id=_DOC,
        base_id=_BASE,
        record_generation=1,
    )
    port = FakeArticleRagSearchPort(
        outcomes=[
            ArticleRagSearchOutcome(
                status="ok",
                summary="ok",
                hits=(hit,),
                rag_substrate_id=_RUN,
                plan_content_sha256=_PLAN,
                stable_document_id=_DOC,
                base_id=_BASE,
                record_generation=1,
            )
        ]
    )
    result, consumed = await execute_search_current_article(
        envelope=envelope,
        tool_input=SearchCurrentArticleToolInput(query="main idea"),
        article_rag=port,
        fence=_ok_fence,
        registry=registry,
        search_calls_so_far=0,
    )
    assert result.status == "ok"
    assert consumed is True
    assert port.call_count == 1
    assert len(result.evidence_handles) >= 1


@pytest.mark.asyncio
async def test_search_executor_second_call_budget_exhausted_no_io() -> None:
    envelope = _envelope_stub()
    registry = _registry_for(envelope)
    port = FakeArticleRagSearchPort(
        outcomes=[
            ArticleRagSearchOutcome(status="ok", summary="should not run", hits=())
        ]
    )
    result, consumed = await execute_search_current_article(
        envelope=envelope,
        tool_input=SearchCurrentArticleToolInput(query="again"),
        article_rag=port,
        fence=_ok_fence,
        registry=registry,
        search_calls_so_far=1,
        max_search_calls=1,
    )
    assert result.status == "budget_exhausted"
    assert consumed is False
    assert port.call_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    ["empty", "not_ready", "not_indexed", "indexing", "unavailable"],
)
async def test_search_executor_non_ok_statuses_pass_through(status: str) -> None:
    """Non-ok RAG statuses are typed and must not raise / invent evidence."""
    envelope = _envelope_stub()
    registry = _registry_for(envelope)
    port = FakeArticleRagSearchPort(
        outcomes=[
            ArticleRagSearchOutcome(
                status=status,  # type: ignore[arg-type]
                summary=f"status={status}",
                detail_code=f"test_{status}",
            )
        ]
    )
    result, consumed = await execute_search_current_article(
        envelope=envelope,
        tool_input=SearchCurrentArticleToolInput(query="q"),
        article_rag=port,
        fence=_ok_fence,
        registry=registry,
        search_calls_so_far=0,
    )
    assert result.status == status
    assert consumed is True
    assert port.call_count == 1
    assert result.evidence_handles == []


@pytest.mark.asyncio
async def test_adapter_identity_mismatch_discards_without_hits() -> None:
    from dataclasses import dataclass, field

    @dataclass
    class _Hit:
        chunk_id: str = "c1"
        text: str = "t"
        citation: dict[str, Any] = field(default_factory=dict)
        metadata_json: dict[str, Any] = field(default_factory=dict)
        score: float = 0.9
        content_sha256: str = _CHUNK

    @dataclass
    class _Result:
        reading_record_id: UUID = _RECORD
        stable_document_id: UUID = _DOC
        base_id: UUID = field(default_factory=uuid4)  # mismatch
        record_generation: int = 1
        plan_content_sha256: str = _PLAN
        index_run_id: UUID = _RUN
        hits: tuple[Any, ...] = ()

    @dataclass
    class _Retrieval:
        call_count: int = 0

        async def retrieve_for_record(self, **kwargs: Any) -> _Result:
            del kwargs
            self.call_count += 1
            hit = _Hit(
                citation={
                    "reading_record_id": str(_RECORD),
                    "stable_document_id": str(_DOC),
                    "base_id": str(_BASE),
                    "record_generation": 1,
                    "canonical_text_start_utf16": 0,
                    "canonical_text_end_utf16": 5,
                    "block_ids": [],
                    "unit_ids": [],
                    "anchor_segment_ids": [],
                },
                metadata_json={
                    "source_scope": "main_reading_text",
                    "block_type": "paragraph",
                },
            )
            return _Result(hits=(hit,))

    retrieval = _Retrieval()
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
    assert outcome.detail_code == "identity_mismatch"
    assert outcome.hits == ()
    assert retrieval.call_count == 1


# ---------------------------------------------------------------------------
# C. Legacy isolation — static import fence
# ---------------------------------------------------------------------------


def test_reader_record_ask_package_never_imports_legacy_prompt_integration() -> None:
    """New Ask path must not import ArticleRagPromptIntegration (or reader_ask agent)."""
    forbidden_substrings = (
        "article_rag_prompt_integration",
        "ArticleRagPromptIntegration",
        "reader_ask.agent",
        "reader_ask.ask_runtime",
        "load_render_scene",
    )
    py_files = sorted(_RR_ASK_ROOT.glob("*.py"))
    assert py_files, f"expected package files under {_RR_ASK_ROOT}"
    offenders: list[str] = []
    for path in py_files:
        source = path.read_text(encoding="utf-8")
        # AST walk for imports
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            offenders.append(f"{path.name}: syntax error")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    joined = alias.name
                    if any(f in joined for f in forbidden_substrings):
                        offenders.append(f"{path.name}: import {joined}")
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if any(f in mod for f in forbidden_substrings):
                    offenders.append(f"{path.name}: from {mod}")
                for alias in node.names:
                    if any(f in alias.name for f in forbidden_substrings):
                        offenders.append(f"{path.name}: from {mod} import {alias.name}")
        # Also ban string-level dynamic imports of the legacy bridge.
        for needle in (
            "article_rag_prompt_integration",
            "ArticleRagPromptIntegration",
        ):
            if needle in source:
                # Allow comments that explicitly say "do not import …"
                for line in source.splitlines():
                    stripped = line.strip()
                    if needle in stripped and not stripped.startswith("#"):
                        # Docstrings may mention the name as a negative constraint.
                        if "does not import" in stripped or "without importing" in stripped:
                            continue
                        if "not import" in stripped or "Never" in stripped:
                            continue
                        if '"""' in stripped or "'''" in stripped:
                            continue
                        offenders.append(f"{path.name}: source mentions {needle}")
    assert offenders == [], "legacy leakage:\n" + "\n".join(offenders)
