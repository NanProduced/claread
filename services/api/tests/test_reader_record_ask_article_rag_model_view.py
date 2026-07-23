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
from app.services.reader_record_ask.evidence import (
    EvidenceHandleRef,
    ServerEvidenceObservation,
)
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
        match=r"rag_model_view_rollback_failed code=batch_complete",
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
        match=r"rag_model_view_rollback_failed code=batch_complete",
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
        match=r"rag_model_view_rollback_failed code=batch_partial",
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
# R4-A5-5R P1-1: outcome-level identity fence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field, bad_value",
    [
        ("base_id", UUID("99999999-9999-9999-9999-999999999999")),
        ("record_generation", 99),
        ("stable_document_id", UUID("88888888-8888-8888-8888-888888888888")),
    ],
)
def test_outcome_identity_fence_blocks_even_perfect_hits(
    field: str, bad_value: object
) -> None:
    """Outcome-level base/generation/stable-document mismatch → fixed safe
    unavailable view even though every hit matches the envelope."""
    kwargs: dict[str, object] = {
        "base_id": _BASE,
        "record_generation": 1,
        "stable_document_id": _DOC,
        field: bad_value,
    }
    hit_text = "完全匹配 envelope 的 hit 正文。"
    outcome = ArticleRagSearchOutcome(
        status="ok",
        summary="upstream summary",
        hits=(_hit(text=hit_text),),
        rag_substrate_id=_SUBSTRATE,
        plan_content_sha256=_PLAN_HASH,
        base_id=kwargs["base_id"],  # type: ignore[arg-type]
        record_generation=kwargs["record_generation"],  # type: ignore[arg-type]
        stable_document_id=kwargs["stable_document_id"],  # type: ignore[arg-type]
    )
    budget = ModelVisibleTurnBudget()
    registry = EvidenceRegistry(_FINGERPRINT)
    before = budget.snapshot()

    result = assemble_rag_model_view(
        outcome=outcome,
        envelope_identity=_identity(),
        registry=registry,
        budget=budget,
    )

    assert result.kind == "unavailable"
    assert result.model_visible is True
    rendered = result.rendered_tool_view.text
    parsed = json.loads(rendered)
    assert parsed["status"] == "unavailable"
    assert parsed["evidence_handles"] == []
    assert parsed["article_text_blocks"] == []
    # No hit processed or registered.
    assert len(registry) == 0
    assert result.evidence_handles == ()
    # No leakage: hit body, identity values, hashes, substrate.
    assert hit_text not in rendered
    assert str(bad_value) not in rendered
    assert str(_SUBSTRATE) not in rendered
    assert _PLAN_HASH not in rendered
    assert _CONTENT_HASH not in rendered
    # Fixed internal detail code (sidecar only).
    assert result.sidecar is not None
    assert result.sidecar.detail_code == "outcome_identity_mismatch"
    assert result.sidecar.hits == ()
    # Metered safe view; nothing else charged.
    assert budget.spent("rag") == before["rag"] + len(rendered)


def test_outcome_identity_complete_match_ok_path_unchanged() -> None:
    """Fully matching outcome identity: the ok path does not regress."""
    budget = ModelVisibleTurnBudget()
    registry = EvidenceRegistry(_FINGERPRINT)
    result = assemble_rag_model_view(
        outcome=_ok_outcome(_hit()),
        envelope_identity=_identity(),
        registry=registry,
        budget=budget,
    )
    assert result.kind == "ok"
    assert len(registry) == 1
    assert len(result.evidence_handles) == 1


def test_outcome_fence_safe_view_budget_denied_zero_mutation() -> None:
    """Fence mismatch + not even the safe view fits → typed non-model-
    visible budget_denied with zero mutation."""
    renderer = ModelViewRenderer()
    budget = ModelVisibleTurnBudget()
    budget.charge("rag", renderer.render_plain("f" * (RESERVE_RAG - 1)))
    registry = EvidenceRegistry(_FINGERPRINT)
    before = budget.snapshot()
    outcome = ArticleRagSearchOutcome(
        status="ok",
        summary="upstream summary",
        hits=(_hit(),),
        rag_substrate_id=_SUBSTRATE,
        plan_content_sha256=_PLAN_HASH,
        base_id=_BASE,
        record_generation=1,
        stable_document_id=UUID("88888888-8888-8888-8888-888888888888"),
    )
    result = assemble_rag_model_view(
        outcome=outcome,
        envelope_identity=_identity(),
        registry=registry,
        budget=budget,
    )
    assert result.kind == "budget_denied"
    assert result.model_visible is False
    assert result.rendered_tool_view is None
    assert result.charge is None
    assert budget.snapshot() == before
    assert len(registry) == 0
    assert result.sidecar is not None
    assert result.sidecar.detail_code == "outcome_identity_mismatch"


# ---------------------------------------------------------------------------
# R4-A5-5R P1-2: batch compensation completeness (no short-circuit)
# ---------------------------------------------------------------------------

_HIT_A_TEXT = "第一个段落的内容甲。"
_HIT_B_TEXT = "第二个段落的内容乙。"


class _RagTwoHitWrongHandleSecondRegistry(EvidenceRegistry):
    """obs1 registers normally; obs2 writes then returns a wrong handle;
    discarding obs1 reports mismatch (foreign simulation); obs2 discards
    normally via super()."""

    wrong_handle = "evh_" + ("ee" * 16)
    mismatch_secret = "PROBE_RAG_DISCARD_MISMATCH_SECRET_3c8b"

    def __init__(self, fp: str) -> None:
        super().__init__(fp)
        self.first_handle: str | None = None

    def register(self, observation: ServerEvidenceObservation):  # type: ignore[override]
        ref = super().register(observation)
        if self.first_handle is None:
            self.first_handle = observation.handle.handle_id
            return ref
        return EvidenceHandleRef(handle_id=self.wrong_handle)

    def discard_if_matches(self, *, handle_id, expected):  # type: ignore[override]
        if handle_id == self.first_handle:
            return "mismatch"
        return super().discard_if_matches(handle_id=handle_id, expected=expected)


class _RagTwoHitDiscardRaiseFirstRegistry(_RagTwoHitWrongHandleSecondRegistry):
    """As above, but discarding obs1 RAISES instead of returning mismatch."""

    raise_secret = "PROBE_RAG_DISCARD_RAISE_SECRET_6d21"

    def discard_if_matches(self, *, handle_id, expected):  # type: ignore[override]
        if handle_id == self.first_handle:
            raise RuntimeError(self.raise_secret)
        return EvidenceRegistry.discard_if_matches(
            self, handle_id=handle_id, expected=expected
        )


class _RagTwoHitSecondWriteThenRaiseRegistry(EvidenceRegistry):
    """obs1 registers normally; obs2 writes via super() then raises."""

    raise_secret = "PROBE_RAG_SECOND_WRITE_RAISE_SECRET_a5f2"

    def __init__(self, fp: str) -> None:
        super().__init__(fp)
        self.calls = 0

    def register(self, observation: ServerEvidenceObservation):  # type: ignore[override]
        self.calls += 1
        ref = super().register(observation)
        if self.calls == 2:
            raise RuntimeError(self.raise_secret)
        return ref


class _RefundFailingBudget(ModelVisibleTurnBudget):
    """Budget whose host-only refund seam raises (refund-failure probe)."""

    raise_secret = "PROBE_RAG_REFUND_RAISE_SECRET_9e14"

    def _refund_chars(self, account, cost):  # type: ignore[override]
        raise RuntimeError(self.raise_secret)


def test_batch_first_mismatch_kept_second_still_cleaned_full_refund() -> None:
    """Two hits: obs1 registers, obs2 postcondition fails; obs1 discard
    reports mismatch → obs1 must NOT be deleted, but obs2 cleanup still
    runs (no short-circuit); full refund; stable code; no leakage."""
    registry = _RagTwoHitWrongHandleSecondRegistry(_FINGERPRINT)
    budget = ModelVisibleTurnBudget()
    before = budget.snapshot()
    hit_a = _hit(text=_HIT_A_TEXT)
    hit_b = _hit(text=_HIT_B_TEXT)

    with pytest.raises(RuntimeError) as exc_info:
        assemble_rag_model_view(
            outcome=_ok_outcome(hit_a, hit_b),
            envelope_identity=_identity(),
            registry=registry,
            budget=budget,
        )

    message = str(exc_info.value)
    assert message == "rag_model_view_rollback_failed code=batch_partial"
    # Foreign-simulated first entry kept; second entry cleaned.
    assert len(registry) == 1
    kept = registry.list_observations()[0]
    assert kept.handle.handle_id == registry.first_handle
    assert kept.handle.handle_id != registry.wrong_handle
    assert kept.snippet == _HIT_A_TEXT
    assert all(obs.snippet != _HIT_B_TEXT for obs in registry.list_observations())
    # Full refund exactly once.
    assert budget.snapshot() == before
    # No probe secret / body / handle / UUID / hash in the message.
    for forbidden in (
        registry.mismatch_secret,
        _HIT_A_TEXT,
        _HIT_B_TEXT,
        kept.handle.handle_id,
        registry.wrong_handle,
        str(_RECORD),
        str(_BASE),
        str(_DOC),
        str(_SUBSTRATE),
        _CONTENT_HASH,
        _PLAN_HASH,
    ):
        assert forbidden not in message


def test_batch_first_discard_raise_still_cleans_second_and_refunds() -> None:
    """obs1 discard RAISES: cleanup must continue to obs2 and still refund;
    aggregate verdict stays fail-closed (batch_partial)."""
    registry = _RagTwoHitDiscardRaiseFirstRegistry(_FINGERPRINT)
    budget = ModelVisibleTurnBudget()
    before = budget.snapshot()

    with pytest.raises(RuntimeError) as exc_info:
        assemble_rag_model_view(
            outcome=_ok_outcome(_hit(text=_HIT_A_TEXT), _hit(text=_HIT_B_TEXT)),
            envelope_identity=_identity(),
            registry=registry,
            budget=budget,
        )

    message = str(exc_info.value)
    assert message == "rag_model_view_rollback_failed code=batch_partial"
    # obs1 kept (raise → unproven, never deleted); obs2 cleaned.
    assert len(registry) == 1
    assert registry.list_observations()[0].snippet == _HIT_A_TEXT
    assert all(
        obs.snippet != _HIT_B_TEXT for obs in registry.list_observations()
    )
    assert budget.snapshot() == before
    assert _RagTwoHitDiscardRaiseFirstRegistry.raise_secret not in message
    assert _HIT_A_TEXT not in message and _HIT_B_TEXT not in message


def test_batch_second_register_write_then_raise_both_attempted_cleaned() -> None:
    """obs2 register writes then raises: BOTH attempted observations enter
    cleanup → provably clean → batch_complete; registry empty; refunded."""
    registry = _RagTwoHitSecondWriteThenRaiseRegistry(_FINGERPRINT)
    budget = ModelVisibleTurnBudget()
    before = budget.snapshot()

    with pytest.raises(RuntimeError) as exc_info:
        assemble_rag_model_view(
            outcome=_ok_outcome(_hit(text=_HIT_A_TEXT), _hit(text=_HIT_B_TEXT)),
            envelope_identity=_identity(),
            registry=registry,
            budget=budget,
        )

    message = str(exc_info.value)
    assert message == "rag_model_view_rollback_failed code=batch_complete"
    # No transaction residue: both attempted observations removed.
    assert len(registry) == 0
    assert budget.snapshot() == before
    assert _RagTwoHitSecondWriteThenRaiseRegistry.raise_secret not in message
    assert _HIT_A_TEXT not in message and _HIT_B_TEXT not in message


def test_batch_refund_failure_stable_code_registry_clean() -> None:
    """Single hit, register write-then-raise, refund seam raises:
    registry cleanup still completes; aggregate code batch_refund;
    no probe secret leakage."""
    registry = _RagWriteThenRaiseRegistry(_FINGERPRINT)
    registry.fail_after_write = True
    budget = _RefundFailingBudget()

    with pytest.raises(RuntimeError) as exc_info:
        assemble_rag_model_view(
            outcome=_ok_outcome(_hit(text=_HIT_A_TEXT)),
            envelope_identity=_identity(),
            registry=registry,
            budget=budget,
        )

    message = str(exc_info.value)
    assert message == "rag_model_view_rollback_failed code=batch_refund"
    # Registry cleanup happened despite the refund failure.
    assert len(registry) == 0
    assert _RefundFailingBudget.raise_secret not in message
    assert _RagWriteThenRaiseRegistry.fail_message not in message
    assert _HIT_A_TEXT not in message


def test_batch_partial_and_refund_failure_stable_code() -> None:
    """mismatch cleanup AND refund failure → batch_partial_and_refund."""
    registry = _RagTwoHitWrongHandleSecondRegistry(_FINGERPRINT)
    budget = _RefundFailingBudget()

    with pytest.raises(RuntimeError) as exc_info:
        assemble_rag_model_view(
            outcome=_ok_outcome(_hit(text=_HIT_A_TEXT), _hit(text=_HIT_B_TEXT)),
            envelope_identity=_identity(),
            registry=registry,
            budget=budget,
        )

    message = str(exc_info.value)
    assert (
        message
        == "rag_model_view_rollback_failed code=batch_partial_and_refund"
    )
    # obs2 cleaned even though refund will fail; obs1 kept (mismatch).
    assert len(registry) == 1
    assert registry.list_observations()[0].snippet == _HIT_A_TEXT
    assert _RefundFailingBudget.raise_secret not in message
    assert registry.mismatch_secret not in message
    assert _HIT_A_TEXT not in message and _HIT_B_TEXT not in message


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


def test_rag_assembler_wired_only_via_turn_coordinator() -> None:
    """R4-A5-7: assemble_rag_model_view is owned by TurnCoordinator."""
    import app.services.reader_record_ask.agent as agent_mod
    import app.services.reader_record_ask.runtime as runtime_mod
    import app.services.reader_record_ask.turn_coordinator as coord_mod

    agent_src = open(agent_mod.__file__, encoding="utf-8").read()
    runtime_src = open(runtime_mod.__file__, encoding="utf-8").read()
    coord_src = open(coord_mod.__file__, encoding="utf-8").read()
    assert "assemble_rag_model_view" not in agent_src
    assert "assemble_rag_model_view" not in runtime_src
    assert "assemble_rag_model_view" in coord_src
    # Agent must not call the legacy search executor.
    assert "execute_search_current_article" not in agent_src
    assert "search_current_article_executor" not in agent_src


def test_legacy_prompt_integration_not_used() -> None:
    import app.services.reader_record_ask.article_rag_model_view as mod

    source = open(mod.__file__, encoding="utf-8").read()
    assert "ArticleRagPromptIntegration" not in source
