"""D6-I4R: Article RAG Ask Runtime Hardening.

Covers:
  1. Factory happy-path strict test — when config is complete,
     ``build_default_article_rag_prompt_integration`` MUST return
     an ``ArticleRagPromptIntegration`` (not None).  Lower
     factories are monkeypatched to avoid network / env deps.
  2. Real factory no-network smoke — construction must not
     access the network.
  3. Sidecar helper ``_apply_article_rag_sidecar_to_runtime_state``
     behaviour: copy semantics, metadata overlay, no-attach,
     repair merge.
  4. Integrate no-network runtime smoke — attach / no-attach /
     fail-soft paths with a fake provider + real bridge.

All tests are no-network: no real LLM, DashScope, Zilliz, DB.
"""

from __future__ import annotations

import copy
import json
import socket
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from app.agents.reader_ask_agent import ReaderAskRuntimeState, build_reader_ask_prompt
from app.services.reader_ask.article_rag_prompt_integration import (
    ArticleRagPromptIntegration,
    ArticleRagSidecar,
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
# Constants
# ---------------------------------------------------------------------------

_RECORD_ID = UUID("00000000-0000-0000-0000-000000000001")
_USER_ID = UUID("00000000-0000-0000-0000-000000000002")
_QUERY_TEXT = "what is the main idea of this article"


# ---------------------------------------------------------------------------
# Fake settings
# ---------------------------------------------------------------------------


class _FakeSettings:
    """Minimal settings stand-in for the factory."""

    def __init__(self, **kwargs: Any) -> None:
        self._values: dict[str, Any] = {
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


def _make_complete_settings() -> _FakeSettings:
    """Settings with all config present for a happy-path factory call."""
    return _FakeSettings(
        reader_article_rag_enabled=True,
        reader_article_rag_zilliz_uri="https://zilliz.example.com",
        reader_article_rag_zilliz_token="zilliz-secret-token",
        reader_article_rag_zilliz_collection="article_rag_index_v1",
        reader_article_rag_embedding_provider="dashscope",
        reader_article_rag_embedding_model="text-embedding-v3",
        reader_article_rag_vector_provider="zilliz",
    )


# ---------------------------------------------------------------------------
# Assembly / payload fixtures
# ---------------------------------------------------------------------------


def _make_attach_assembly() -> ArticleRagAskPromptAssembly:
    return ArticleRagAskPromptAssembly(
        kind="article_rag_context",
        should_attach=True,
        prompt_attachment_block="[RAG context text here]",
        citations=(
            {
                "citation_id": "rag-cite-1",
                "kind": "article_rag_context",
                "label": "Paragraph 1",
            },
        ),
        context_ids=("ctx-1",),
        source_pack_hash="abc123",
        query_sha256="a" * 64,
        status="available",
        failure_code=None,
        retryable=True,
        fallback_allowed=True,
        metadata_json={
            "stable_document_id": "doc-1",
            "omitted_hit_count": 0,
            "budget_exceeded": False,
        },
    )


def _make_no_attach_assembly() -> ArticleRagAskPromptAssembly:
    return ArticleRagAskPromptAssembly(
        kind="article_rag_context",
        should_attach=False,
        prompt_attachment_block="",
        citations=(),
        context_ids=(),
        source_pack_hash=None,
        query_sha256=None,
        status="not_indexed_or_unavailable",
        failure_code="context_no_indexed_run",
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


# ---------------------------------------------------------------------------
# Fake provider
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


def _build_integration(
    *,
    provider: Any | None = None,
    bridge: Any | None = None,
) -> ArticleRagPromptIntegration:
    return ArticleRagPromptIntegration(
        provider=provider,
        bridge=bridge if bridge is not None else ArticleRagAskPromptBridge(),
    )


# ===========================================================================
# 1. Factory happy-path strict test
# ===========================================================================


class TestFactoryHappyPath:
    """When config is complete, the factory MUST return an
    ``ArticleRagPromptIntegration``, not None."""

    def test_factory_returns_integration_when_config_complete(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Monkeypatch the lower embedding/vector factories to
        return fakes, then verify the factory returns a non-None
        ``ArticleRagPromptIntegration``."""
        # Patch the lower factories at their source modules so the
        # factory's local ``from ... import`` picks up the fakes.
        import app.services.reader_orchestration.article_rag_embedding_provider as emb_mod
        import app.services.reader_orchestration.article_rag_vector_search as vs_mod

        fake_embedding = SimpleNamespace(embed="fake")
        fake_searcher = SimpleNamespace(search="fake")

        monkeypatch.setattr(
            emb_mod,
            "build_default_article_rag_embedding_provider",
            lambda settings: fake_embedding,
        )
        monkeypatch.setattr(
            vs_mod,
            "build_default_article_rag_vector_searcher",
            lambda settings: fake_searcher,
        )

        settings = _make_complete_settings()
        result = build_default_article_rag_prompt_integration(settings)

        assert result is not None, (
            "Factory must return ArticleRagPromptIntegration when "
            "config is complete (enabled + zilliz uri/token/collection "
            "+ embedding/vector provider configured)"
        )
        assert isinstance(result, ArticleRagPromptIntegration)

    def test_factory_returns_none_when_disabled(self) -> None:
        settings = _FakeSettings(reader_article_rag_enabled=False)
        result = build_default_article_rag_prompt_integration(settings)
        assert result is None

    def test_factory_returns_none_when_zilliz_uri_missing(self) -> None:
        settings = _FakeSettings(
            reader_article_rag_enabled=True,
            reader_article_rag_zilliz_uri="",
            reader_article_rag_zilliz_token="some-token",
            reader_article_rag_zilliz_collection="some-collection",
        )
        result = build_default_article_rag_prompt_integration(settings)
        assert result is None

    def test_factory_returns_none_when_zilliz_token_missing(self) -> None:
        settings = _FakeSettings(
            reader_article_rag_enabled=True,
            reader_article_rag_zilliz_uri="https://example.com",
            reader_article_rag_zilliz_token="",
            reader_article_rag_zilliz_collection="some-collection",
        )
        result = build_default_article_rag_prompt_integration(settings)
        assert result is None

    def test_factory_returns_none_when_zilliz_collection_missing(self) -> None:
        settings = _FakeSettings(
            reader_article_rag_enabled=True,
            reader_article_rag_zilliz_uri="https://example.com",
            reader_article_rag_zilliz_token="some-token",
            reader_article_rag_zilliz_collection="",
        )
        result = build_default_article_rag_prompt_integration(settings)
        assert result is None

    def test_factory_does_not_leak_token_in_logs(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The factory's debug logs must NOT echo the Zilliz
        token, URI, or query_text."""
        import logging

        caplog.set_level(
            logging.DEBUG,
            logger="app.services.reader_ask.article_rag_prompt_integration",
        )

        # Trigger the incomplete-config debug log path.
        settings = _FakeSettings(
            reader_article_rag_enabled=True,
            reader_article_rag_zilliz_uri="https://secret-uri.example.com",
            reader_article_rag_zilliz_token="super-secret-token",
            reader_article_rag_zilliz_collection="",  # missing → logs
        )
        build_default_article_rag_prompt_integration(settings)

        full_log = caplog.text
        assert "super-secret-token" not in full_log
        assert "secret-uri.example.com" not in full_log


# ===========================================================================
# 2. Real factory no-network smoke
# ===========================================================================


class TestFactoryNoNetworkSmoke:
    """The real factory (no monkeypatched lower factories) must
    not access the network during construction."""

    def test_factory_construction_does_not_access_network(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Patch ``socket.socket.connect`` to raise if any network
        call is attempted, then call the real factory.  The factory
        must complete (return None or integration) without raising
        a network error."""

        def _refuse_connect(*args: Any, **kwargs: Any) -> None:
            raise AssertionError(
                "Factory construction must not access the network; "
                f"socket.connect called with args={args!r}"
            )

        # Patch socket at the lowest level.  We only block connect,
        # not other socket operations, to avoid breaking stdlib
        # internals that don't actually reach the network.
        monkeypatch.setattr(socket.socket, "connect", _refuse_connect)

        settings = _make_complete_settings()
        # The factory must not raise — it may return None (if env
        # deps are missing) or an integration.  Either is fine for
        # this smoke test; the key assertion is no network access.
        result = build_default_article_rag_prompt_integration(settings)
        assert result is None or isinstance(result, ArticleRagPromptIntegration)


# ===========================================================================
# 3. Sidecar helper behaviour
# ===========================================================================


class TestApplySidecarHelper:
    """Tests for ``_apply_article_rag_sidecar_to_runtime_state``."""

    def test_attach_path_writes_all_fields(self) -> None:
        """On the attach path, citations / context_ids / metadata
        are all written to runtime_state."""
        from app.services.reader_ask.service import (
            _apply_article_rag_sidecar_to_runtime_state,
        )

        sidecar = ArticleRagSidecar(
            should_attach=True,
            citations=({"citation_id": "rag-1"},),
            context_ids=("ctx-1",),
            source_pack_hash="hash-1",
            query_sha256="sha-1",
            status="available",
            failure_code=None,
            retryable=True,
            fallback_allowed=True,
            metadata_json={"stable_document_id": "doc-1"},
        )
        state = ReaderAskRuntimeState()

        _apply_article_rag_sidecar_to_runtime_state(state, sidecar)

        assert state.article_rag_citations == [{"citation_id": "rag-1"}]
        assert state.article_rag_context_ids == ["ctx-1"]
        assert state.article_rag_metadata["should_attach"] is True
        assert state.article_rag_metadata["source_pack_hash"] == "hash-1"
        assert state.article_rag_metadata["query_sha256"] == "sha-1"
        assert state.article_rag_metadata["status"] == "available"
        assert state.article_rag_metadata["stable_document_id"] == "doc-1"

    def test_metadata_json_overlaid_by_top_level(self) -> None:
        """Top-level authoritative fields must override same-named
        keys in ``metadata_json``."""
        from app.services.reader_ask.service import (
            _apply_article_rag_sidecar_to_runtime_state,
        )

        # metadata_json has a conflicting "status" key.
        sidecar = ArticleRagSidecar(
            should_attach=True,
            status="available",  # top-level authoritative
            metadata_json={
                "status": "stale-from-metadata",
                "stable_document_id": "doc-1",
            },
        )
        state = ReaderAskRuntimeState()

        _apply_article_rag_sidecar_to_runtime_state(state, sidecar)

        # Top-level wins.
        assert state.article_rag_metadata["status"] == "available"
        # Non-conflicting metadata key is preserved.
        assert state.article_rag_metadata["stable_document_id"] == "doc-1"

    def test_copies_not_aliases(self) -> None:
        """Modifying the sidecar's citations / metadata after
        calling the helper must NOT affect runtime_state."""
        from app.services.reader_ask.service import (
            _apply_article_rag_sidecar_to_runtime_state,
        )

        mutable_citation = {"citation_id": "rag-1"}
        sidecar = ArticleRagSidecar(
            should_attach=True,
            citations=(mutable_citation,),
            context_ids=("ctx-1",),
            metadata_json={"stable_document_id": "doc-1"},
        )
        state = ReaderAskRuntimeState()

        _apply_article_rag_sidecar_to_runtime_state(state, sidecar)

        # Mutate the original dict — runtime_state must be unaffected.
        mutable_citation["citation_id"] = "tampered"
        assert state.article_rag_citations[0]["citation_id"] == "rag-1"

        # The runtime_state's citation is a different dict object.
        assert state.article_rag_citations[0] is not mutable_citation

    def test_no_attach_path_empty_citations(self) -> None:
        """On the no-attach path, citations / context_ids are empty
        but metadata retains status / failure_code for ops."""
        from app.services.reader_ask.service import (
            _apply_article_rag_sidecar_to_runtime_state,
        )

        sidecar = ArticleRagSidecar(
            should_attach=False,
            citations=(),
            context_ids=(),
            status="not_indexed_or_unavailable",
            failure_code="context_no_indexed_run",
            retryable=True,
            fallback_allowed=True,
        )
        state = ReaderAskRuntimeState()

        _apply_article_rag_sidecar_to_runtime_state(state, sidecar)

        assert state.article_rag_citations == []
        assert state.article_rag_context_ids == []
        assert state.article_rag_metadata["should_attach"] is False
        assert state.article_rag_metadata["status"] == "not_indexed_or_unavailable"
        assert state.article_rag_metadata["failure_code"] == "context_no_indexed_run"

    def test_empty_sidecar_clears_runtime_state(self) -> None:
        """Calling the helper with an empty sidecar clears any
        previously-written article_rag_* fields."""
        from app.services.reader_ask.service import (
            _apply_article_rag_sidecar_to_runtime_state,
        )

        state = ReaderAskRuntimeState()
        state.article_rag_citations = [{"citation_id": "old-cite"}]
        state.article_rag_context_ids = ["old-ctx"]
        state.article_rag_metadata = {"should_attach": True}

        _apply_article_rag_sidecar_to_runtime_state(state, ArticleRagSidecar.empty())

        assert state.article_rag_citations == []
        assert state.article_rag_context_ids == []
        assert state.article_rag_metadata["should_attach"] is False


class TestRepairMergeClearsArticleRag:
    """``_merge_repair_runtime_state`` must clear article_rag_*
    because the repair payload bypassed RAG integration."""

    def test_repair_merge_clears_all_three_fields(self) -> None:
        from app.services.reader_ask.service import _merge_repair_runtime_state

        target = ReaderAskRuntimeState()
        target.article_rag_citations = [{"citation_id": "stale-cite"}]
        target.article_rag_context_ids = ["stale-ctx"]
        target.article_rag_metadata = {"should_attach": True}

        repair = ReaderAskRuntimeState()
        repair.article_rag_citations = [{"citation_id": "deepcopy-cite"}]

        _merge_repair_runtime_state(target, repair)

        assert target.article_rag_citations == []
        assert target.article_rag_context_ids == []
        assert target.article_rag_metadata == {}


# ===========================================================================
# 4. Integrate no-network runtime smoke
# ===========================================================================


class TestIntegrateNoNetworkSmoke:
    """End-to-end smoke for ``integrate()`` with a fake provider
    and the real bridge.  No network, no DB."""

    @pytest.mark.asyncio
    async def test_attach_path_smoke(self) -> None:
        """Attach path: payload user_message is augmented with RAG
        envelope; citation JSON does NOT enter the serialized
        prompt; sidecar carries citations."""
        integration = _build_integration(
            provider=_FakeProvider(assembly=_make_attach_assembly()),
        )
        payload = _make_base_payload()

        result = await integration.integrate(
            prompt_payload=payload,
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text=_QUERY_TEXT,
        )

        # Payload user_message was augmented.
        assert ATTACHMENT_BEGIN_MARKER in result.payload["user_message"]
        assert ATTACHMENT_END_MARKER in result.payload["user_message"]

        # Citation JSON does NOT appear in the serialized prompt.
        serialized = json.dumps(result.payload, ensure_ascii=False)
        assert "rag-cite-1" not in serialized
        assert "article_rag" not in result.payload

        # Sidecar carries citations.
        assert result.sidecar.should_attach is True
        assert len(result.sidecar.citations) == 1
        assert result.sidecar.citations[0]["citation_id"] == "rag-cite-1"

    @pytest.mark.asyncio
    async def test_attach_path_build_reader_ask_prompt_no_citation_leak(self) -> None:
        """The final prompt built by ``build_reader_ask_prompt``
        must NOT contain citation JSON."""
        integration = _build_integration(
            provider=_FakeProvider(assembly=_make_attach_assembly()),
        )
        payload = _make_base_payload()

        result = await integration.integrate(
            prompt_payload=payload,
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text=_QUERY_TEXT,
        )

        final_prompt = build_reader_ask_prompt(
            SimpleNamespace(payload=result.payload)  # type: ignore[arg-type]
        )
        assert "rag-cite-1" not in final_prompt
        assert '"article_rag"' not in final_prompt
        assert '"citations"' not in final_prompt

    @pytest.mark.asyncio
    async def test_no_attach_path_smoke(self) -> None:
        """No-attach path: payload is returned unchanged."""
        integration = _build_integration(
            provider=_FakeProvider(assembly=_make_no_attach_assembly()),
        )
        payload = _make_base_payload()
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
    async def test_fail_soft_provider_exception_smoke(self) -> None:
        """Fail-soft: provider raises → payload unchanged, sidecar
        empty."""
        integration = _build_integration(provider=_RaisingProvider())
        payload = _make_base_payload()
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
    async def test_sidecar_flows_to_runtime_state_via_helper(self) -> None:
        """End-to-end: integrate() → sidecar → helper →
        runtime_state.  Verifies the full output-side wiring."""
        from app.services.reader_ask.service import (
            _apply_article_rag_sidecar_to_runtime_state,
        )

        integration = _build_integration(
            provider=_FakeProvider(assembly=_make_attach_assembly()),
        )
        payload = _make_base_payload()

        result = await integration.integrate(
            prompt_payload=payload,
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text=_QUERY_TEXT,
        )

        state = ReaderAskRuntimeState()
        _apply_article_rag_sidecar_to_runtime_state(state, result.sidecar)

        assert len(state.article_rag_citations) == 1
        assert state.article_rag_citations[0]["citation_id"] == "rag-cite-1"
        assert state.article_rag_context_ids == ["ctx-1"]
        assert state.article_rag_metadata["should_attach"] is True
        assert state.article_rag_metadata["stable_document_id"] == "doc-1"

    @pytest.mark.asyncio
    async def test_query_text_not_in_result_repr(self) -> None:
        """query_text must not appear in the result's repr."""
        integration = _build_integration(
            provider=_FakeProvider(assembly=_make_attach_assembly()),
        )
        result = await integration.integrate(
            prompt_payload=_make_base_payload(),
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text=_QUERY_TEXT,
        )
        assert _QUERY_TEXT not in repr(result)
        assert _QUERY_TEXT not in repr(result.sidecar)
