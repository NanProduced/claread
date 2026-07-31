"""Tests for memory block rendering (R0.1 §6 注入形态约束 + §8.3 §7).

A1 stub: 待 A1 完成后移除（schema/mapping 走 conftest 注入的 _stub）
"""

from __future__ import annotations

from app.services.reader_record_ask.model_view_budget import (
    is_renderer_minted_view,
)
from app.services.reader_record_ask.thread_memory.render import (
    render_compaction_notice,
    render_memory_block,
)
from app.services.reader_record_ask.thread_memory.schema import (
    Episode,
    SourceBinding,
    StructuredFact,
    ThreadMemorySnapshot,
)


def _binding(
    binding_id: str,
    *,
    source_type: str = "article",
    status: str = "valid",
) -> SourceBinding:
    return SourceBinding(
        binding_id=binding_id,
        source_type=source_type,
        source_id="doc_1" if source_type == "article" else "https://example.com",
        fence_type="stable_document" if source_type == "article" else "reading_record",
        fence_values={
            "reading_record_id": "r1",
            "stable_document_id": "doc_1" if source_type == "article" else None,
            "base_id": "b1",
            "record_generation": 1,
        },
        validity_check={"status": status, "last_validated_turn": 0},
    )


def _fact(
    fact_id: str,
    *,
    text: str = "x",
    source_type: str = "user_question",
    source_ids: list[str] | None = None,
    confidence: str = "medium",
    turn_origin: int = 1,
    protected: bool = False,
) -> StructuredFact:
    return StructuredFact(
        fact_id=fact_id,
        text=text,
        source_type=source_type,
        source_ids=source_ids if source_ids is not None else [fact_id],
        confidence=confidence,
        turn_origin=turn_origin,
        protected=protected,
    )


def _episode(
    facts: list[StructuredFact],
    bindings: list[SourceBinding] | None = None,
    *,
    episode_id: str = "ep_1_1",
    turn_range: dict | None = None,
) -> Episode:
    return Episode(
        episode_id=episode_id,
        turn_range=turn_range or {"start": 1, "end": 1},
        structured_facts=facts,
        source_bindings=bindings or [],
        excluded_content_markers=["reasoning"],
        compaction_model="none",
        compaction_method="emergency_deterministic",
        compaction_timestamp="2026-07-30T00:00:00Z",
        compaction_input_watermark="",
    )


def _snapshot(episodes: list[Episode]) -> ThreadMemorySnapshot:
    return ThreadMemorySnapshot(
        version="thread_memory_v1",
        watermark="w",
        thread_id="t1",
        created_at="2026-07-30T00:00:00Z",
        last_compacted_at="2026-07-30T00:00:00Z",
        episodes=episodes,
    )


# ---------------------------------------------------------------------------
# render_memory_block — XML fence + structure
# ---------------------------------------------------------------------------


def test_render_memory_block_wraps_in_xml_fence():
    facts = [_fact("f1", text="Q1", source_type="user_question")]
    snap = _snapshot([_episode(facts)])
    view = render_memory_block(snap, budget_chars=1000)
    assert view is not None
    assert view.text.startswith(
        '<transcript_data role="data" not_instructions="true">'
    )
    assert view.text.endswith("</transcript_data>")


def test_render_memory_block_returns_renderer_minted_view():
    facts = [_fact("f1", text="Q1")]
    snap = _snapshot([_episode(facts)])
    view = render_memory_block(snap, budget_chars=1000)
    assert view is not None
    assert is_renderer_minted_view(view)
    assert view.char_cost == len(view.text)


def test_render_memory_block_returns_none_for_none_snapshot():
    assert render_memory_block(None, budget_chars=1000) is None


def test_render_memory_block_returns_none_for_zero_or_negative_budget():
    snap = _snapshot([_episode([_fact("f1")])])
    assert render_memory_block(snap, budget_chars=0) is None
    assert render_memory_block(snap, budget_chars=-10) is None


def test_render_memory_block_returns_none_for_empty_episodes():
    snap = _snapshot([])
    assert render_memory_block(snap, budget_chars=1000) is None


# ---------------------------------------------------------------------------
# Article invalid binding → "此前讨论过（来源已变化）", no citation_ids
# ---------------------------------------------------------------------------


def test_render_memory_block_article_invalid_binding_renders_prior_mention():
    """Article fact bound to an invalid binding → prior_mention text, no citation_ids."""
    facts = [
        _fact(
            "f1",
            text="The author argues X.",
            source_type="article",
            source_ids=["m1", "bind1"],
            confidence="high",
        )
    ]
    bindings = [_binding("bind1", status="invalid")]
    snap = _snapshot([_episode(facts, bindings)])
    view = render_memory_block(snap, budget_chars=2000)
    assert view is not None
    assert "此前讨论过（来源已变化）" in view.text
    # The original fact text must NOT appear (avoid leaking stale claims).
    assert "The author argues X." not in view.text
    # No citation_id leaked.
    assert "bind1" not in view.text


def test_render_memory_block_article_valid_binding_renders_text():
    facts = [
        _fact(
            "f1",
            text="The author argues X.",
            source_type="article",
            source_ids=["m1", "bind1"],
            confidence="high",
        )
    ]
    bindings = [_binding("bind1", status="valid")]
    snap = _snapshot([_episode(facts, bindings)])
    view = render_memory_block(snap, budget_chars=2000)
    assert view is not None
    assert "The author argues X." in view.text


# ---------------------------------------------------------------------------
# Web fact → "线索" prefix
# ---------------------------------------------------------------------------


def test_render_memory_block_web_fact_renders_with_clue_prefix():
    facts = [
        _fact(
            "f1",
            text="NYT reported Y.",
            source_type="web",
            confidence="prior_context",
        )
    ]
    snap = _snapshot([_episode(facts)])
    view = render_memory_block(snap, budget_chars=2000)
    assert view is not None
    assert "线索：NYT reported Y." in view.text


# ---------------------------------------------------------------------------
# user_correction → [已纠正] annotation
# ---------------------------------------------------------------------------


def test_render_memory_block_user_correction_annotated():
    facts = [
        _fact(
            "f1",
            text="The actual cause is Z.",
            source_type="user_correction",
            confidence="high",
            protected=True,
        )
    ]
    snap = _snapshot([_episode(facts)])
    view = render_memory_block(snap, budget_chars=2000)
    assert view is not None
    assert "[已纠正]" in view.text
    assert "The actual cause is Z." in view.text


# ---------------------------------------------------------------------------
# Budget shrinking — prior_context evicted before high; protected kept
# ---------------------------------------------------------------------------


def test_render_memory_block_evicts_prior_context_before_high():
    """When budget is tight, prior_context (web) facts are dropped first."""
    facts = [
        _fact(
            "f_web",
            text="A" * 100,
            source_type="web",
            confidence="prior_context",
            turn_origin=1,
        ),
        _fact(
            "f_high",
            text="B" * 100,
            source_type="assistant_answer",
            confidence="high",
            turn_origin=2,
        ),
    ]
    snap = _snapshot([_episode(facts)])
    # Budget large enough for only one fact body.
    view = render_memory_block(snap, budget_chars=350)
    assert view is not None
    assert "B" * 100 in view.text  # high kept
    assert "A" * 100 not in view.text  # prior_context evicted


def test_render_memory_block_protected_fact_never_evicted():
    """protected=True facts survive even when confidence is low."""
    facts = [
        _fact(
            "f_protected",
            text="PROTECTED" * 10,
            source_type="user_correction",
            confidence="high",
            protected=True,
            turn_origin=1,
        ),
        _fact(
            "f_high",
            text="HIGH" * 20,
            source_type="assistant_answer",
            confidence="high",
            turn_origin=2,
        ),
    ]
    snap = _snapshot([_episode(facts)])
    # Tight budget that can only fit the protected fact.
    view = render_memory_block(snap, budget_chars=300)
    assert view is not None
    assert "PROTECTED" in view.text


def test_render_memory_block_budget_shrinking_keeps_high_over_medium():
    """medium confidence evicted before high."""
    facts = [
        _fact(
            "f_med",
            text="M" * 50,
            source_type="user_question",
            confidence="medium",
            turn_origin=1,
        ),
        _fact(
            "f_high",
            text="H" * 50,
            source_type="assistant_answer",
            confidence="high",
            turn_origin=2,
        ),
    ]
    snap = _snapshot([_episode(facts)])
    # R1.6 P1-3: budget tuned so only the high-confidence fact fits.
    # At 300 both facts fit; at 200 the inner budget (128) covers the
    # header (31) + high fact (81) = 112, leaving 16 — medium (78) evicted.
    view = render_memory_block(snap, budget_chars=200)
    assert view is not None
    assert "H" * 50 in view.text  # high kept
    assert "M" * 50 not in view.text  # medium evicted


# ---------------------------------------------------------------------------
# render_compaction_notice
# ---------------------------------------------------------------------------


def test_render_compaction_notice_model_method():
    notice = render_compaction_notice(method="model", duration_ms=3200)
    assert "对话记忆已整理" in notice
    assert "Conversation memory organized" in notice
    # No token counts / percentages / context meter.
    assert "%" not in notice
    assert "token" not in notice.lower()


def test_render_compaction_notice_emergency_method():
    notice = render_compaction_notice(
        method="emergency_deterministic", duration_ms=500
    )
    assert "整理遇到问题，已使用备用方案" in notice
    assert "Using backup method" in notice
    assert "%" not in notice


def test_render_compaction_notice_window_shrink_method():
    notice = render_compaction_notice(method="window_shrink", duration_ms=100)
    assert "整理遇到问题，已使用备用方案" in notice


def test_render_compaction_notice_hybrid_method_uses_model_wording():
    notice = render_compaction_notice(method="hybrid", duration_ms=2000)
    assert "对话记忆已整理" in notice


def test_render_compaction_notice_does_not_leak_duration():
    """duration_ms is accepted but never surfaced (frozen #7)."""
    notice = render_compaction_notice(method="model", duration_ms=9876)
    assert "9876" not in notice
    assert "ms" not in notice
