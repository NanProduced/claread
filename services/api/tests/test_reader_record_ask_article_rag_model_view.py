"""R4-A5-5: Article RAG tool model-view scrub (offline core).

Behavior tests: six port statuses fail-soft, ok hits through a single
untrusted block + real JSON metering, sidecar-only provenance, identity
mismatch discard, budget denial + register-failure atomicity, zero I/O
and reverse wiring guards. Public seams only.
"""

from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest

from app.services.reader_record_ask.article_rag_model_view import (
    RAG_BLOCK_ROLE,
    RagEnvelopeIdentity,
    assemble_rag_model_view,
)
from app.services.reader_record_ask.article_rag_port import (
    ArticleRagHitView,
    ArticleRagSearchOutcome,
)
from app.services.reader_record_ask.evidence import ServerEvidenceObservation
from app.services.reader_record_ask.evidence_registry import EvidenceRegistry
from app.services.reader_record_ask.model_view_budget import (
    RESERVE_RAG,
    ModelViewRenderer,
    ModelVisibleTurnBudget,
)

_FINGERPRINT = "a" * 64
_RECORD = UUID("22222222-2222-2222-2222-222222222222")
_BASE = UUID("33333333-3333-3333-3333-333333333333")
_DOC = UUID("44444444-4444-4444-4444-444444444444")
_SUBSTRATE = UUID("77777777-7777-7777-7777-777777777777")
_PLAN_HASH = "ab" * 32
_CONTENT_HASH = "cd" * 32


def _identity() -> RagEnvelopeIdentity:
    return RagEnvelopeIdentity(
        envelope_fingerprint=_FINGERPRINT,
        reading_record_id=_RECORD,
        base_id=_BASE,
        record_generation=1,
        stable_document_id=_DOC,
    )


def _hit(
    *,
    text: str = "匹配到的文章段落正文。",
    record: UUID = _RECORD,
    base: UUID = _BASE,
    generation: int = 1,
    doc: UUID = _DOC,
    scope: str = "main_reading_text",
) -> ArticleRagHitView:
    return ArticleRagHitView(
        chunk_id="chunk-1",
        text=text,
        source_scope=scope,
        block_type="paragraph",
        content_sha256=_CONTENT_HASH,
        canonical_text_start_utf16=0,
        canonical_text_end_utf16=42,
        score=0.91,
        reading_record_id=record,
        stable_document_id=doc,
        base_id=base,
        record_generation=generation,
    )


def _ok_outcome(*hits: ArticleRagHitView) -> ArticleRagSearchOutcome:
    return ArticleRagSearchOutcome(
        status="ok",
        summary="upstream summary with detail that must not leak",
        hits=tuple(hits),
        rag_substrate_id=_SUBSTRATE,
        plan_content_sha256=_PLAN_HASH,
        stable_document_id=_DOC,
        base_id=_BASE,
        record_generation=1,
    )


# ---------------------------------------------------------------------------
# Six statuses fail-soft
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status",
    ["empty", "not_ready", "not_indexed", "indexing", "unavailable"],
)
def test_non_ok_statuses_fail_soft_metered_no_mutation(status: str) -> None:
    budget = ModelVisibleTurnBudget()
    registry = EvidenceRegistry(_FINGERPRINT)
    outcome = ArticleRagSearchOutcome(
        status=status,  # type: ignore[arg-type]
        summary="upstream raw detail 内部错误",
        detail_code="upstream_secret_code",
        hits=(_hit(),),  # must be ignored for non-ok
    )
    result = assemble_rag_model_view(
        outcome=outcome,
        envelope_identity=_identity(),
        registry=registry,
        budget=budget,
    )
    assert result.kind == status
    assert result.model_visible is True
    assert result.rendered_tool_view is not None
    assert result.charge is not None and result.charge.account == "rag"
    parsed = json.loads(result.rendered_tool_view.text)
    assert parsed["status"] == status
    assert parsed["evidence_handles"] == []
    assert parsed["article_text_blocks"] == []
    # No upstream detail / detail_code leakage.
    assert "upstream raw detail" not in result.rendered_tool_view.text
    assert "upstream_secret_code" not in result.rendered_tool_view.text
    # Zero registry mutation; no citeable handles.
    assert len(registry) == 0
    assert result.evidence_handles == ()
    # Sidecar keeps server-only diagnostics.
    assert result.sidecar is not None
    assert result.sidecar.detail_code == "upstream_secret_code"


# ---------------------------------------------------------------------------
# ok path: single untrusted appearance, real JSON metering, sidecar
# ---------------------------------------------------------------------------


def test_ok_hit_single_untrusted_appearance_and_real_json_cost() -> None:
    budget = ModelVisibleTurnBudget()
    registry = EvidenceRegistry(_FINGERPRINT)
    text = "文章中的关键段落。"
    result = assemble_rag_model_view(
        outcome=_ok_outcome(_hit(text=text)),
        envelope_identity=_identity(),
        registry=registry,
        budget=budget,
    )
    assert result.kind == "ok"
    assert result.model_visible is True
    rendered = result.rendered_tool_view.text
    parsed = json.loads(rendered)
    # One handle, one block, aligned.
    assert len(parsed["evidence_handles"]) == 1
    assert len(parsed["article_text_blocks"]) == 1
    block = parsed["article_text_blocks"][0]
    assert f'role="{RAG_BLOCK_ROLE}"' in block
    # Text appears exactly once across the whole tool-view.
    assert rendered.count(text) == 1
    # Metered at the real complete JSON cost.
    assert result.charge is not None
    assert result.charge.account == "rag"
    assert result.charge.cost == len(rendered) <= RESERVE_RAG
    # Registered search_hit observation with rag citation truth.
    assert len(registry) == 1
    handle_id = parsed["evidence_handles"][0]["handle_id"]
    obs = registry.get(handle_id)
    assert obs is not None
    assert obs.handle.kind == "search_hit"
    assert obs.rag_citation is not None
    assert obs.rag_citation.rag_substrate_id == str(_SUBSTRATE)
    assert obs.snippet == text
    # Result handles match the registry.
    assert result.evidence_handles[0].handle_id == handle_id


def test_ok_sidecar_only_provenance_never_model_visible() -> None:
    budget = ModelVisibleTurnBudget()
    registry = EvidenceRegistry(_FINGERPRINT)
    result = assemble_rag_model_view(
        outcome=_ok_outcome(_hit()),
        envelope_identity=_identity(),
        registry=registry,
        budget=budget,
    )
    rendered = result.rendered_tool_view.text
    # score / chunk id / hashes / UUIDs / provenance are sidecar-only.
    assert "0.91" not in rendered
    assert "chunk-1" not in rendered
    assert _CONTENT_HASH not in rendered
    assert _PLAN_HASH not in rendered
    assert str(_SUBSTRATE) not in rendered
    assert str(_DOC) not in rendered
    assert str(_BASE) not in rendered
    assert str(_RECORD) not in rendered
    assert "canonical_text_start" not in rendered
    # Sidecar carries them (server-only).
    sidecar = result.sidecar
    assert sidecar is not None
    assert sidecar.hits and sidecar.hits[0].chunk_id == "chunk-1"
    assert sidecar.hits[0].handle_id is not None
    assert sidecar.hits[0].rag_substrate_id == _SUBSTRATE


# ---------------------------------------------------------------------------
# Identity mismatch discard + zero mutation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_hit",
    [
        _hit(record=uuid4()),
        _hit(base=uuid4()),
        _hit(generation=99),
        _hit(doc=uuid4()),
        _hit(scope="footnote"),
    ],
)
def test_identity_mismatch_hit_discarded_without_mutation(
    bad_hit: ArticleRagHitView,
) -> None:
    budget = ModelVisibleTurnBudget()
    registry = EvidenceRegistry(_FINGERPRINT)
    before = budget.snapshot()
    result = assemble_rag_model_view(
        outcome=_ok_outcome(bad_hit),
        envelope_identity=_identity(),
        registry=registry,
        budget=budget,
    )
    # All hits mismatch → empty fail-soft view; no registration/charge
    # beyond the safe empty view.
    assert result.kind == "empty"
    assert len(registry) == 0
    assert result.sidecar is not None
    assert result.sidecar.discarded_identity_mismatches == 1
    assert budget.spent("rag") == len(result.rendered_tool_view.text)
    assert before["rag"] == 0


def test_mixed_hits_keep_only_identity_verified() -> None:
    budget = ModelVisibleTurnBudget()
    registry = EvidenceRegistry(_FINGERPRINT)
    good = _hit(text="好段落。")
    bad = _hit(text="坏段落。", record=uuid4())
    result = assemble_rag_model_view(
        outcome=_ok_outcome(good, bad),
        envelope_identity=_identity(),
        registry=registry,
        budget=budget,
    )
    assert result.kind == "ok"
    assert len(result.evidence_handles) == 1
    assert len(registry) == 1
    assert result.sidecar is not None
    assert result.sidecar.discarded_identity_mismatches == 1
    rendered = result.rendered_tool_view.text
    assert "好段落。" in rendered
    assert "坏段落。" not in rendered


# ---------------------------------------------------------------------------
# Budget denial + register/rollback atomicity
# ---------------------------------------------------------------------------


def test_budget_denial_zero_mutation() -> None:
    renderer = ModelViewRenderer()
    budget = ModelVisibleTurnBudget()
    budget.charge("rag", renderer.render_plain("f" * (RESERVE_RAG - 1)))
    registry = EvidenceRegistry(_FINGERPRINT)
    before = budget.snapshot()
    result = assemble_rag_model_view(
        outcome=_ok_outcome(_hit(text="x" * 1500)),
        envelope_identity=_identity(),
        registry=registry,
        budget=budget,
    )
    assert result.kind == "budget_denied"
    assert result.model_visible is False
    assert result.rendered_tool_view is None
    assert result.charge is None
    assert result.evidence_handles == ()
    assert len(registry) == 0
    assert budget.snapshot() == before


def test_budget_fit_keeps_largest_hit_prefix() -> None:
    renderer = ModelViewRenderer()
    budget = ModelVisibleTurnBudget()
    # Leave room for about one hit view but not all three.
    budget.charge("rag", renderer.render_plain("f" * 2600))
    registry = EvidenceRegistry(_FINGERPRINT)
    result = assemble_rag_model_view(
        outcome=_ok_outcome(
            _hit(text="第一个段落正文。"),
            _hit(text="第二个段落正文。"),
            _hit(text="第三个段落正文。"),
        ),
        envelope_identity=_identity(),
        registry=registry,
        budget=budget,
    )
    assert result.kind == "ok"
    assert 1 <= len(result.evidence_handles) < 3
    assert len(registry) == len(result.evidence_handles)
    assert budget.spent("rag") <= RESERVE_RAG
    # The kept prefix is the FIRST hits (deterministic ordering).
    rendered = result.rendered_tool_view.text
    assert "第一个段落正文。" in rendered


class _RagWriteThenRaiseRegistry(EvidenceRegistry):
    """Writes via super().register then raises (partial commit probe)."""

    fail_message = "PROBE_RAG_REGISTER_AFTER_WRITE_SECRET_71aa"
    fail_after_write: bool = False

    def register(self, observation: ServerEvidenceObservation):  # type: ignore[override]
        ref = super().register(observation)
        if self.fail_after_write:
            raise RuntimeError(self.fail_message)
        return ref


class _RagWrongHandleRegistry(EvidenceRegistry):
    """Writes the observation but returns a different legal handle."""

    wrong_handle = "evh_" + ("ee" * 16)

    def register(self, observation: ServerEvidenceObservation):  # type: ignore[override]
        super().register(observation)
        from app.services.reader_record_ask.evidence import EvidenceHandleRef

        return EvidenceHandleRef(handle_id=self.wrong_handle)


def test_register_write_then_raise_rolls_back_budget_and_registry() -> None:
    registry = _RagWriteThenRaiseRegistry(_FINGERPRINT)
    budget = ModelVisibleTurnBudget()
    before = budget.snapshot()
    registry.fail_after_write = True
    with pytest.raises(
        RuntimeError,
        match=r"rag_model_view_failed code=register",
    ):
        assemble_rag_model_view(
            outcome=_ok_outcome(_hit(text="段落一。"), _hit(text="段落二。")),
            envelope_identity=_identity(),
            registry=registry,
            budget=budget,
        )
    # Budget refunded; no observation lingers; probe secret not in code.
    assert budget.snapshot() == before
    assert budget.spent("rag") == 0
    assert len(registry) == 0


def test_register_postcondition_failure_rolls_back_fully() -> None:
    registry = _RagWrongHandleRegistry(_FINGERPRINT)
    budget = ModelVisibleTurnBudget()
    before = budget.snapshot()
    with pytest.raises(
        RuntimeError,
        match=r"rag_model_view_failed code=register",
    ):
        assemble_rag_model_view(
            outcome=_ok_outcome(_hit()),
            envelope_identity=_identity(),
            registry=registry,
            budget=budget,
        )
    assert budget.snapshot() == before
    assert len(registry) == 0
    assert registry.get(_RagWrongHandleRegistry.wrong_handle) is None


class _RagMismatchDiscardRegistry(EvidenceRegistry):
    """Postcondition fail + discard reports mismatch (foreign entry)."""

    wrong_handle = "evh_" + ("dd" * 16)

    def register(self, observation: ServerEvidenceObservation):  # type: ignore[override]
        super().register(observation)
        from app.services.reader_record_ask.evidence import EvidenceHandleRef

        return EvidenceHandleRef(handle_id=self.wrong_handle)

    def discard_if_matches(self, *, handle_id, expected):  # type: ignore[override]
        return "mismatch"


def test_rollback_mismatch_fails_closed_with_refund() -> None:
    registry = _RagMismatchDiscardRegistry(_FINGERPRINT)
    budget = ModelVisibleTurnBudget()
    before = budget.snapshot()
    with pytest.raises(
        RuntimeError,
        match=r"rag_model_view_rollback_failed code=registry_mismatch",
    ):
        assemble_rag_model_view(
            outcome=_ok_outcome(_hit()),
            envelope_identity=_identity(),
            registry=registry,
            budget=budget,
        )
    # Budget refunded despite the unproven registry; foreign entry kept.
    assert budget.snapshot() == before
    assert len(registry) == 1


# ---------------------------------------------------------------------------
# Zero I/O + reverse wiring guards
# ---------------------------------------------------------------------------


def test_rag_assembler_source_has_no_io_or_model_retry() -> None:
    import app.services.reader_record_ask.article_rag_model_view as mod

    source = open(mod.__file__, encoding="utf-8").read()
    assert "ModelRetry" not in source
    assert "from pydantic_ai" not in source
    assert "DocumentAccess" not in source
    assert "zilliz" not in source.lower()
    assert "embedding" not in source.lower()
    assert "httpx" not in source
    assert "sqlalchemy" not in source.lower()
    # The assembler consumes outcomes; it never calls the port protocol.
    assert "search_current_article(" not in source


def test_rag_assembler_not_wired_into_runtime_this_round() -> None:
    import app.services.reader_record_ask.agent as agent_mod
    import app.services.reader_record_ask.production_stream as stream_mod
    import app.services.reader_record_ask.production_wiring as wiring_mod
    import app.services.reader_record_ask.runtime as runtime_mod
    import app.services.reader_record_ask.runtime_deps as deps_mod
    import app.services.reader_record_ask.service as service_mod
    import app.services.reader_record_ask.sse as sse_mod

    for wired in (
        runtime_mod,
        deps_mod,
        stream_mod,
        wiring_mod,
        service_mod,
        sse_mod,
        agent_mod,
    ):
        source = open(wired.__file__, encoding="utf-8").read()
        assert "article_rag_model_view" not in source


def test_legacy_prompt_integration_not_used() -> None:
    import app.services.reader_record_ask.article_rag_model_view as mod

    source = open(mod.__file__, encoding="utf-8").read()
    assert "ArticleRagPromptIntegration" not in source
