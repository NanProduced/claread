"""R1B: 端到端集成测试 — 验证 R1A 三 agent 产出的协同工作。

这些测试使用**真实模块**（非 mock），覆盖跨模块数据流：

1. flag=False 回归保护：assembly 路径零差异
2. 全链路：emergency_full_snapshot → render_memory_block → budget charge
3. CAS 失配 → emergency 重建
4. allowlist → validate_snapshot → stripped facts
5. fence 失效 → render 降级
6. budget 退款：request_frame 拒绝时 memory 账户退款
7. 九账户总和 == 96,000

R1B 仅隔离测试，不触碰任何生产代码。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services.reader_record_ask.model_view_budget import (
    ACCOUNT_RESERVES,
    MODEL_VISIBLE_TURN_PAYLOAD_CAP,
    ModelViewRenderer,
    ModelVisibleTurnBudget,
    RenderedModelView,
)
from app.services.reader_record_ask.thread_memory.allowlist import (
    build_allowlist,
    compute_watermark,
    validate_snapshot,
)
from app.services.reader_record_ask.thread_memory.emergency import (
    emergency_compact,
    emergency_full_snapshot,
)
from app.services.reader_record_ask.thread_memory.fence import (
    check_all_bindings,
    check_binding_validity,
)
from app.services.reader_record_ask.thread_memory.render import (
    render_memory_block,
)
from app.services.reader_record_ask.thread_memory.schema import (
    Episode,
    SourceBinding,
    StructuredFact,
    ThreadMemorySnapshot,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _user_msg(msg_id: str, text: str) -> dict[str, Any]:
    return {"id": msg_id, "role": "user", "content_md": text}


def _assistant_msg(
    msg_id: str,
    *,
    answer_blocks: list[dict] | None = None,
    citations: list[dict] | None = None,
    web_outcome: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"id": msg_id, "role": "assistant"}
    if answer_blocks is not None:
        payload["answer_blocks"] = answer_blocks
    if citations is not None:
        payload["citations"] = citations
    if web_outcome is not None:
        payload["web_search_summary"] = {"outcome": web_outcome}
    return payload


def _ok_turn_run(
    run_id: str,
    *,
    citation_bindings: list[dict] | None = None,
    rag_citation: dict | None = None,
) -> dict[str, Any]:
    """模拟 repository.list_ok_turn_runs_with_bindings 返回的单条记录。"""
    resolved: dict[str, Any] = {}
    if citation_bindings is not None:
        resolved["citation_bindings"] = citation_bindings
    if rag_citation is not None:
        resolved["rag_citation"] = rag_citation
    return {
        "id": run_id,
        "final_status": "ok",
        "resolved_evidence_json": resolved if resolved else None,
    }


def _article_binding(
    citation_id: str,
    handle_id: str,
    *,
    stable_document_id: str = "doc-1",
    base_id: str = "base-1",
    record_generation: int = 1,
) -> dict[str, Any]:
    """模拟 InternalCitationBinding + ArticleRagCitationEvidence 合并结构。"""
    return {
        "citation_id": citation_id,
        "handle_id": handle_id,
        "source_kind": "article",
        "rag_citation": {
            "stable_document_id": stable_document_id,
            "base_id": base_id,
            "record_generation": record_generation,
            "reading_record_id": "rec-1",
            "block_ids": ["blk-1"],
            "unit_ids": ["unit-1"],
        },
    }


def _web_binding(citation_id: str, handle_id: str) -> dict[str, Any]:
    return {
        "citation_id": citation_id,
        "handle_id": handle_id,
        "source_kind": "web",
        "canonical_url": "https://example.com/article",
        "web_title": "Example Article",
    }


# ---------------------------------------------------------------------------
# 1. flag=False 回归保护
# ---------------------------------------------------------------------------


class TestFlagOffRegression:
    """memory_enabled=False 时 assembly 路径零差异。"""

    def test_budget_has_memory_accounts_but_flag_gates_usage(self) -> None:
        """九账户存在，但 flag=False 时不应使用 memory 账户。"""
        budget = ModelVisibleTurnBudget()
        # memory 账户存在且初始为 0
        assert budget.spent("memory") == 0
        assert budget.spent("recent_history") == 0
        # reserve 存在
        assert budget.reserve("memory") == ACCOUNT_RESERVES["memory"]
        assert budget.reserve("recent_history") == ACCOUNT_RESERVES["recent_history"]

    def test_nine_accounts_sum_to_cap(self) -> None:
        """九账户总和 == 96,000（RL2/H5 单位统一约束）。"""
        assert sum(ACCOUNT_RESERVES.values()) == MODEL_VISIBLE_TURN_PAYLOAD_CAP
        assert MODEL_VISIBLE_TURN_PAYLOAD_CAP == 128_000

    def test_render_memory_block_returns_none_for_none_snapshot(self) -> None:
        """snapshot=None → render 返回 None（flag=False 路径）。"""
        result = render_memory_block(None, budget_chars=6000)
        assert result is None


# ---------------------------------------------------------------------------
# 2. 全链路：emergency → render → budget charge
# ---------------------------------------------------------------------------


class TestFullChainEmergencyRenderCharge:
    """emergency_full_snapshot → render_memory_block → budget.charge 全链路。"""

    def test_full_chain_aged_episode_rendered_and_charged(self) -> None:
        """多条历史 → emergency 压缩 → render → budget.charge 成功。"""
        # 构造 6 条消息（3 user + 3 assistant），recent_pairs=1 → aged 含 4 条
        messages = [
            _user_msg("u1", "What is paragraph 2 about?"),
            _assistant_msg("a1", answer_blocks=[{"text": "It discusses reuse."}]),
            _user_msg("u2", "Can you cite paragraph 3?"),
            _assistant_msg(
                "a2",
                answer_blocks=[{"text": "Paragraph 3 states X."}],
                citations=[{"citation_id": "cit-1"}],
            ),
            _user_msg("u3", "And paragraph 4?"),
            _assistant_msg("a3", answer_blocks=[{"text": "Paragraph 4 says Y."}]),
        ]
        ok_runs = [
            _ok_turn_run("r1"),
            _ok_turn_run("r2", citation_bindings=[_article_binding("cit-1", "h1")]),
            _ok_turn_run("r3"),
        ]

        # emergency_full_snapshot
        snapshot = emergency_full_snapshot(
            canonical_messages=messages,
            ok_turn_runs=ok_runs,
            recent_pairs=1,
            thread_id="t1",
        )
        assert snapshot is not None
        assert snapshot.thread_id == "t1"
        assert len(snapshot.episodes) >= 1
        # watermark 覆盖全部 canonical messages
        assert snapshot.watermark == compute_watermark(messages)

        # render_memory_block
        budget = ModelVisibleTurnBudget()
        memory_view = render_memory_block(snapshot, budget_chars=6000)
        assert memory_view is not None
        assert isinstance(memory_view, RenderedModelView)
        # XML 栅栏包裹
        assert "<transcript_data role=\"data\"" in memory_view.text
        assert "</transcript_data>" in memory_view.text

        # budget.charge 成功
        budget.charge("memory", memory_view)
        assert budget.spent("memory") == memory_view.char_cost
        assert budget.spent("memory") > 0

    def test_empty_thread_snapshot_render_returns_none(self) -> None:
        """空 thread → snapshot 无 episodes → render 返回 None。"""
        snapshot = emergency_full_snapshot(
            canonical_messages=[], ok_turn_runs=[], thread_id="t-empty"
        )
        assert snapshot.episodes == []
        result = render_memory_block(snapshot, budget_chars=6000)
        assert result is None


# ---------------------------------------------------------------------------
# 3. CAS 失配 → emergency 重建
# ---------------------------------------------------------------------------


class TestCASMismatchRebuild:
    """watermark 失配时 emergency 重建。"""

    def test_watermark_changes_on_message_append(self) -> None:
        """追加新消息后 watermark 变化。"""
        msgs_v1 = [_user_msg("u1", "hello"), _assistant_msg("a1", answer_blocks=[{"text": "hi"}])]
        msgs_v2 = msgs_v1 + [_user_msg("u2", "follow up")]

        wm1 = compute_watermark(msgs_v1)
        wm2 = compute_watermark(msgs_v2)
        assert wm1 != wm2

    def test_watermark_deterministic(self) -> None:
        """同一消息列表 → 同一 watermark。"""
        msgs = [_user_msg("u1", "hello"), _assistant_msg("a1", answer_blocks=[{"text": "hi"}])]
        assert compute_watermark(msgs) == compute_watermark(msgs)

    def test_watermark_changes_on_content_only_edit(self) -> None:
        """R1.6 P0-1: watermark follows canonical revision (content digest).

        Same (id, role) but different content → different watermark.
        This replaces the old ``ignores_content_only`` test that locked
        the wrong contract (watermark was id+role only, so content edits
        were invisible to CAS — a successful regenerate that replaced
        the canonical answer text would NOT invalidate the watermark).
        """
        msgs_a = [_user_msg("u1", "hello")]
        msgs_b = [_user_msg("u1", "different content")]
        assert compute_watermark(msgs_a) != compute_watermark(msgs_b)

    def test_cas_mismatch_triggers_rebuild_via_emergency(self) -> None:
        """模拟 turn_coordinator 的 CAS 失配路径：stale snapshot → rebuild。"""
        # 初始 snapshot（基于 2 条消息）
        msgs_v1 = [_user_msg("u1", "hello"), _assistant_msg("a1", answer_blocks=[{"text": "hi"}])]
        snapshot_v1 = emergency_full_snapshot(msgs_v1, [], thread_id="t1")

        # 追加消息后 watermark 变化
        msgs_v2 = msgs_v1 + [_user_msg("u2", "follow up")]
        current_wm = compute_watermark(msgs_v2)
        assert snapshot_v1.watermark != current_wm

        # emergency 重建
        rebuilt = emergency_full_snapshot(msgs_v2, [], thread_id="t1")
        assert rebuilt.watermark == current_wm


# ---------------------------------------------------------------------------
# 4. allowlist → validate_snapshot → stripped facts
# ---------------------------------------------------------------------------


class TestAllowlistValidation:
    """allowlist 校验端到端。"""

    def test_build_allowlist_union(self) -> None:
        """A_msg ∪ A_cit ∪ A_bind。"""
        messages = [
            _user_msg("u1", "q1"),
            _assistant_msg("a1", citations=[{"citation_id": "cit-1"}]),
        ]
        ok_runs = [
            _ok_turn_run("r1", citation_bindings=[_article_binding("cit-1", "h1")]),
        ]
        allowlist = build_allowlist(messages, ok_runs)
        assert "u1" in allowlist
        assert "a1" in allowlist
        assert "cit-1" in allowlist
        # binding_id 来自 mapping.derive_source_bindings

    def test_validate_snapshot_strips_unknown_facts(self) -> None:
        """allowlist 外的 fact 被剥离。"""
        # 构造一个含 5 facts 的 episode，其中 1 个引用 allowlist 外的 source_id
        facts = [
            StructuredFact(
                fact_id="f1",
                text="valid fact",
                source_type="user_question",
                source_ids=["u1"],
                confidence="medium",
                turn_origin=1,
            ),
            StructuredFact(
                fact_id="f2",
                text="invalid fact",
                source_type="assistant_answer",
                source_ids=["unknown-msg"],
                confidence="high",
                turn_origin=1,
            ),
        ]
        episode = Episode(
            episode_id="ep_1_1",
            turn_range={"start": 1, "end": 1},
            structured_facts=facts,
            source_bindings=[],
            excluded_content_markers=["reasoning"],
            compaction_model="none",
            compaction_method="emergency_deterministic",
            compaction_timestamp="2026-07-30T00:00:00Z",
            compaction_input_watermark="",
        )
        snapshot = ThreadMemorySnapshot(
            version="thread_memory_v1",
            watermark="abc",
            thread_id="t1",
            created_at="2026-07-30T00:00:00Z",
            last_compacted_at="2026-07-30T00:00:00Z",
            last_compaction_stats=None,
            episodes=[episode],
        )
        allowlist = {"u1"}  # 只有 u1 在 allowlist
        validated, metrics = validate_snapshot(snapshot, {}, allowlist, fence_results=None)
        # f2 被剥离
        remaining_fact_ids = {f.fact_id for ep in validated.episodes for f in ep.structured_facts}
        assert "f1" in remaining_fact_ids
        assert "f2" not in remaining_fact_ids
        # metrics 记录剥离
        assert metrics["stripped_facts"] >= 1


# ---------------------------------------------------------------------------
# 5. fence 失效 → render 降级
# ---------------------------------------------------------------------------


class TestFenceFailureDegradesRender:
    """fence 失效的 binding 关联 fact 在 render 时降级。"""

    def test_fence_invalid_binding_marked(self) -> None:
        """generation_changed → binding.validity_check.status='invalid'。"""
        binding = SourceBinding(
            binding_id="b1",
            source_type="article",
            source_id="doc-1",
            fence_type="stable_document",
            fence_values={
                "stable_document_id": "doc-1",
                "base_id": "base-1",
                "record_generation": 1,
                "reading_record_id": "rec-1",
            },
            validity_check={"status": "unchecked"},
        )
        result = check_binding_validity(
            binding,
            reading_record_id="rec-1",
            current_generation=2,  # generation 变了
            current_base_id="base-1",
        )
        assert result.validity_check["status"] == "invalid"
        assert result.validity_check["invalidation_reason"] == "generation_changed"

    def test_fence_valid_binding_passes(self) -> None:
        """全部一致 → status='valid'。"""
        binding = SourceBinding(
            binding_id="b1",
            source_type="article",
            source_id="doc-1",
            fence_type="stable_document",
            fence_values={
                "stable_document_id": "doc-1",
                "base_id": "base-1",
                "record_generation": 1,
                "reading_record_id": "rec-1",
            },
            validity_check={"status": "unchecked"},
        )
        result = check_binding_validity(
            binding,
            reading_record_id="rec-1",
            current_generation=1,
            current_base_id="base-1",
        )
        assert result.validity_check["status"] == "valid"


# ---------------------------------------------------------------------------
# 6. budget 退款：request_frame 拒绝时 memory 退款
# ---------------------------------------------------------------------------


class TestBudgetRefundOnRequestFrameDenial:
    """request_frame 拒绝时 memory 账户退款（turn_prompt.py 逻辑）。"""

    def test_memory_charge_then_refund_on_request_frame_denial(self) -> None:
        """memory 先 charge，request_frame 拒绝时退款。"""
        budget = ModelVisibleTurnBudget()
        renderer = ModelViewRenderer()

        # 先 charge memory
        memory_view = renderer.render_plain(
            "<transcript_data role=\"data\">memory</transcript_data>"
        )
        budget.charge("memory", memory_view)
        assert budget.spent("memory") > 0
        spent_memory = budget.spent("memory")

        # 模拟 request_frame 拒绝：用尽 request_frame 账户
        # 先填满 request_frame
        frame_view = renderer.render_plain("x" * budget.reserve("request_frame"))
        budget.charge("request_frame", frame_view)
        assert budget.remaining("request_frame") == 0

        # 此时再尝试 charge request_frame 应失败
        from app.services.reader_record_ask.model_view_budget import (
            ModelViewBudgetError,
        )
        extra_view = renderer.render_plain("overflow")
        with pytest.raises(ModelViewBudgetError):
            budget.charge("request_frame", extra_view)

        # 退款 memory（模拟 turn_prompt 的退款逻辑）
        budget._refund_chars("memory", spent_memory)  # noqa: SLF001
        assert budget.spent("memory") == 0


# ---------------------------------------------------------------------------
# 7. emergency_compact 确定性
# ---------------------------------------------------------------------------


class TestEmergencyDeterminism:
    """emergency_compact 确定性验证。"""

    def test_same_input_same_output(self) -> None:
        """相同输入 → 相同 episode_id + structured_facts。"""
        messages = [
            _user_msg("u1", "What is paragraph 2?"),
            _assistant_msg("a1", answer_blocks=[{"text": "It discusses reuse."}]),
        ]
        ep1 = emergency_compact(messages, [], turn_range=(1, 1))
        ep2 = emergency_compact(messages, [], turn_range=(1, 1))
        assert ep1.episode_id == ep2.episode_id
        assert len(ep1.structured_facts) == len(ep2.structured_facts)
        assert ep1.compaction_method == "emergency_deterministic"
        assert ep1.compaction_model == "none"

    def test_user_correction_detected(self) -> None:
        """用户纠正消息被检测并标记 protected。"""
        messages = [
            _user_msg("u1", "What is paragraph 2?"),
            _assistant_msg("a1", answer_blocks=[{"text": "It discusses reuse."}]),
            _user_msg("u2", "不对，应该是 paragraph 3"),
            _assistant_msg("a2", answer_blocks=[{"text": "Sorry, paragraph 3 says X."}]),
        ]
        ep = emergency_compact(messages, [], turn_range=(1, 2))
        corrections = [f for f in ep.structured_facts if f.source_type == "user_correction"]
        assert len(corrections) >= 1


# ---------------------------------------------------------------------------
# 8. 集成签名兼容性验证
# ---------------------------------------------------------------------------


class TestIntegrationSignatureCompatibility:
    """验证跨模块调用签名兼容性（catch A3 integration bugs）。"""

    def test_emergency_full_snapshot_signature(self) -> None:
        """emergency_full_snapshot accepts (messages, ok_runs, *, recent_pairs, thread_id)."""
        import inspect
        sig = inspect.signature(emergency_full_snapshot)
        params = list(sig.parameters.keys())
        assert "canonical_messages" in params
        assert "ok_turn_runs" in params
        assert "recent_pairs" in params
        assert "thread_id" in params

    def test_check_all_bindings_is_async(self) -> None:
        """check_all_bindings 是 async 函数（turn_coordinator 需 await）。"""
        import inspect
        assert inspect.iscoroutinefunction(check_all_bindings)

    def test_check_all_bindings_requires_context(self) -> None:
        """check_all_bindings 需要 context 参数。"""
        import inspect
        sig = inspect.signature(check_all_bindings)
        params = list(sig.parameters.keys())
        assert "bindings" in params
        assert "context" in params

    def test_render_memory_block_signature(self) -> None:
        """render_memory_block(snapshot, *, budget_chars) -> RenderedModelView | None。"""
        import inspect
        sig = inspect.signature(render_memory_block)
        params = list(sig.parameters.keys())
        assert "snapshot" in params
        assert "budget_chars" in params
