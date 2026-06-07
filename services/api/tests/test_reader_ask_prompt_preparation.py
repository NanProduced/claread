"""Tests for reader_ask prompt_preparation: compression, budget, and compaction audit."""

from __future__ import annotations

from typing import Any

from app.schemas.reader_ask import ReaderAskTraceSummary
from app.services.reader_ask.prompt_preparation import (
    _compact_prompt_payload,
    _progressive_compact,
    compute_max_input_budget,
    estimate_token_count,
    inject_compaction_audit,
    prepare_prompt_payload,
    should_emit_compacting,
)


class TestEstimateTokenCount:
    """Token count estimation is consistent and monotonic."""

    def test_small_payload_returns_positive(self) -> None:
        payload = {"key": "value"}
        assert estimate_token_count(payload) >= 1

    def test_cjk_text_uses_different_ratio(self) -> None:
        """CJK text should estimate more tokens per character than ASCII."""
        ascii_payload = {"text": "a" * 100}
        cjk_payload = {"text": "中" * 100}
        # CJK characters take more tokens per character (1.5 chars/token vs 4)
        # so the same character count should yield more estimated tokens
        assert estimate_token_count(cjk_payload) > estimate_token_count(ascii_payload)


class TestComputeMaxInputBudget:
    """Budget calculation matches the expected formula."""

    def test_basic_calculation(self) -> None:
        budget = compute_max_input_budget(
            reserved_points=10,
            tokens_per_point=100,
            budget_buffer_tokens=200,
            min_max_output_tokens=400,
            multiplier_output=2,
        )
        # 10 * 100 - 200 - 400 * 2 = 1000 - 200 - 800 = 0
        assert budget == 0

    def test_positive_budget(self) -> None:
        budget = compute_max_input_budget(
            reserved_points=20,
            tokens_per_point=100,
            budget_buffer_tokens=200,
            min_max_output_tokens=400,
            multiplier_output=2,
        )
        # 20 * 100 - 200 - 400 * 2 = 2000 - 200 - 800 = 1000
        assert budget == 1000

    def test_large_buffer_can_make_budget_negative(self) -> None:
        budget = compute_max_input_budget(
            reserved_points=5,
            tokens_per_point=100,
            budget_buffer_tokens=800,
            min_max_output_tokens=400,
            multiplier_output=2,
        )
        # 5 * 100 - 800 - 400 * 2 = 500 - 800 - 800 = -1100
        assert budget == -1100


class TestShouldEmitCompacting:
    """Compacting emission decision is correct."""

    def test_returns_true_when_over_budget(self) -> None:
        # Create a payload large enough to exceed a tiny budget
        payload = {"text": "x" * 10000}
        assert should_emit_compacting(payload, max_input_budget=1) is True

    def test_returns_false_when_within_budget(self) -> None:
        payload = {"text": "hello"}
        # Use a very large budget
        assert should_emit_compacting(payload, max_input_budget=100000) is False

    def test_returns_false_at_exact_budget(self) -> None:
        """When token count equals budget exactly, no compacting needed."""
        payload = {"text": "hello"}
        tokens = estimate_token_count(payload)
        assert should_emit_compacting(payload, max_input_budget=tokens) is False


class TestInjectCompactionAudit:
    """Compaction audit injection into trace_summary."""

    def test_appends_audit_note(self) -> None:
        trace = ReaderAskTraceSummary(
            notes=["existing_note"],
            tool_steps=[],
            source_labels=[],
            context_sources=[],
        )
        result = inject_compaction_audit(trace, ["history", "vocabulary"])
        assert result is not None
        assert "existing_note" in result.notes
        assert any("context_compaction:history,vocabulary" in n for n in result.notes)

    def test_noop_when_empty_audit(self) -> None:
        trace = ReaderAskTraceSummary(
            notes=["existing"],
            tool_steps=[],
            source_labels=[],
            context_sources=[],
        )
        result = inject_compaction_audit(trace, [])
        # Should return the original trace_summary unchanged
        assert result is trace

    def test_noop_when_no_trace(self) -> None:
        result = inject_compaction_audit(None, ["history"])
        assert result is None

    def test_does_not_mutate_original(self) -> None:
        trace = ReaderAskTraceSummary(
            notes=["original"],
            tool_steps=[],
            source_labels=[],
            context_sources=[],
        )
        original_notes = list(trace.notes)
        inject_compaction_audit(trace, ["history"])
        # Original should not be mutated (model_copy creates a new instance)
        assert trace.notes == original_notes

    def test_single_layer_audit(self) -> None:
        trace = ReaderAskTraceSummary(
            notes=[],
            tool_steps=[],
            source_labels=[],
            context_sources=[],
        )
        result = inject_compaction_audit(trace, ["external_assets"])
        assert result is not None
        assert "context_compaction:external_assets" in result.notes


class TestContextCompactingTiming:
    """Verify that compacting decision happens before compression.

    The contract is: should_emit_compacting() is called BEFORE
    prepare_prompt_payload(). If should_emit_compacting returns True,
    the caller must emit context.compacting SSE event before calling
    prepare_prompt_payload.

    This test verifies the decision logic is consistent: if compacting
    should be emitted, then prepare_prompt_payload will actually apply
    compaction.
    """

    def test_compacting_decision_consistent_with_compaction(self) -> None:
        """If should_emit_compacting returns True, prepare_prompt_payload
        must apply at least one compaction layer."""
        # Create a payload that will exceed a small budget
        large_payload = {
            "history": [{"role": "user", "content_md": f"message {i}"} for i in range(20)],
            "external_asset_contexts": [{"content_md": "x" * 500} for _ in range(5)],
            "record_assets": [{"data": "y" * 300} for _ in range(5)],
            "vocabulary_items": [{"word": f"word{i}"} for i in range(10)],
            "record_insights": [{"insight": f"insight{i}"} for i in range(10)],
            "canonical_context": {"attachments": []},
            "article_overview": "z" * 3000,
        }
        budget = compute_max_input_budget(
            reserved_points=5,
            tokens_per_point=100,
            budget_buffer_tokens=50,
            min_max_output_tokens=100,
            multiplier_output=2,
        )
        # Should need compacting
        assert should_emit_compacting(large_payload, max_input_budget=budget) is True

        # prepare_prompt_payload should apply compaction
        result_payload, _, compaction_audit, _ = prepare_prompt_payload(
            large_payload,
            reserved_points=5,
            tokens_per_point=100,
            multiplier_output=2,
            budget_buffer_tokens=50,
            default_max_output_tokens=400,
            min_max_output_tokens=100,
        )
        # Compaction should have been applied
        assert len(compaction_audit) > 0
        # Result should be smaller
        assert estimate_token_count(result_payload) < estimate_token_count(large_payload)


class TestCompactPromptPayload:
    def test_compact_preserves_system_summary(self) -> None:
        """When _compact_prompt_payload truncates history, system messages
        are preserved and only user/assistant messages are truncated."""
        history = [
            {"role": "system", "content_md": "[History summary] Previous intents: grammar"},
        ] + [
            {"role": "user", "content_md": f"Question {i}"}
            for i in range(10)
        ]
        payload = {"history": history, "other_field": "value"}

        compact = _compact_prompt_payload(payload, max_history=4)

        result_history = compact["history"]
        system_msgs = [m for m in result_history if m.get("role") == "system"]
        assert len(system_msgs) == 1
        assert "[History summary]" in system_msgs[0]["content_md"]

        conv_msgs = [m for m in result_history if m.get("role") != "system"]
        assert len(conv_msgs) == 4
        assert conv_msgs[-1]["content_md"] == "Question 9"


class TestProgressiveCompaction:
    """Test that _progressive_compact applies layers in priority order
    and stops as soon as the budget is met."""

    def _make_large_payload(self) -> dict[str, Any]:
        """Create a payload that exceeds the typical 16000 token budget."""
        return {
            "external_asset_contexts": [
                {"content_md": "External asset " + "x" * 2000} for _ in range(5)
            ],
            "record_assets": [
                {"title": f"Asset {i}", "content": "y" * 1500} for i in range(5)
            ],
            "vocabulary_items": [
                {"word": f"word{i}", "definition": "z" * 1000} for i in range(5)
            ],
            "record_insights": [
                {"insight": "insight " + "w" * 1000} for i in range(5)
            ],
            "history": [
                {"role": "system", "content_md": "[History summary] Previous intents: grammar"},
            ] + [
                {"role": "user", "content_md": f"Question {i} " + "a" * 500}
                for i in range(10)
            ],
            "record_context": {
                "sentence_windows": [
                    {"text": "Sentence " + "s" * 800} for _ in range(8)
                ],
                "source_excerpt": "Source text " + "t" * 20000,
            },
            "article_overview": "Overview " + "o" * 10000,
        }

    def test_payload_over_budget_triggers_compaction(self) -> None:
        payload = self._make_large_payload()
        original_tokens = estimate_token_count(payload)
        assert original_tokens > 16000

        budget = 16000
        result, _audit = _progressive_compact(payload, budget_tokens=budget)
        result_tokens = estimate_token_count(result)
        assert result_tokens <= budget, f"Result {result_tokens} exceeds budget {budget}"
        assert result_tokens < original_tokens

    def test_high_priority_preserved_longer_than_low_priority(self) -> None:
        payload = self._make_large_payload()

        budget = 10000
        result, _audit = _progressive_compact(payload, budget_tokens=budget)

        ext_assets = result.get("external_asset_contexts", [])
        original_ext = len(payload["external_asset_contexts"])
        assert len(ext_assets) < original_ext, "External assets should be trimmed before high-priority fields"

        assert result.get("record_context", {}).get("source_excerpt") is not None

    def test_system_summary_not_dropped_before_conversation(self) -> None:
        payload = self._make_large_payload()
        budget = 10000
        result, _audit = _progressive_compact(payload, budget_tokens=budget)

        history = result.get("history", [])
        system_msgs = [m for m in history if isinstance(m, dict) and m.get("role") == "system"]
        assert len(system_msgs) >= 1
        assert "[History summary]" in system_msgs[0]["content_md"]

    def test_compaction_stops_when_budget_met(self) -> None:
        payload = self._make_large_payload()

        budget = 14000
        result, _audit = _progressive_compact(payload, budget_tokens=budget)

        result_tokens = estimate_token_count(result)
        assert result_tokens <= budget, f"Result {result_tokens} exceeds budget {budget}"

        overview = result.get("article_overview", "")
        assert len(overview) > 0, "Article overview should not be empty with moderate budget"

    def test_multiple_layers_applied_for_tight_budget(self) -> None:
        payload = self._make_large_payload()

        budget = 6000
        result, _audit = _progressive_compact(payload, budget_tokens=budget)
        result_tokens = estimate_token_count(result)

        assert result_tokens <= budget, f"Result {result_tokens} exceeds budget {budget}"

        ext_assets = result.get("external_asset_contexts", [])
        assert len(ext_assets) < len(payload["external_asset_contexts"]), \
            "External assets should be trimmed"

        assert result.get("record_context", {}).get("source_excerpt") is not None

    def test_article_overview_preserved_as_long_as_possible(self) -> None:
        payload = self._make_large_payload()

        budget = 12000
        result, _audit = _progressive_compact(payload, budget_tokens=budget)

        overview = result.get("article_overview", "")
        assert len(overview) > 0, "Article overview should still exist"

    def test_within_budget_payload_unchanged(self) -> None:
        payload = {
            "history": [{"role": "user", "content_md": "Hello"}],
            "record_context": {"source_excerpt": "Short text"},
        }
        result, _audit = _progressive_compact(payload, budget_tokens=16000)
        assert result["history"] == payload["history"]
        assert result["record_context"] == payload["record_context"]


class TestPreparePromptPayloadCompaction:
    """Test that prepare_prompt_payload uses real budget for compaction
    and the final payload fits within the calculated input budget."""

    def test_prepare_prompt_payload_compacts_to_real_budget(self) -> None:
        large_payload = {
            "external_asset_contexts": [
                {"content_md": "x" * 4000} for _ in range(5)
            ],
            "record_assets": [{"title": f"A{i}", "content": "y" * 3000} for i in range(5)],
            "vocabulary_items": [{"word": f"w{i}", "definition": "z" * 2000} for i in range(5)],
            "record_insights": [{"insight": "i" * 2000} for _ in range(5)],
            "history": [{"role": "user", "content_md": "q" * 1000} for _ in range(10)],
            "record_context": {
                "sentence_windows": [{"text": "s" * 1500} for _ in range(8)],
                "source_excerpt": "t" * 40000,
            },
            "article_overview": "o" * 20000,
        }

        original_tokens = estimate_token_count(large_payload)
        assert original_tokens > 30000

        reserved_points = 100
        tokens_per_point = 200
        multiplier_output = 3
        budget_buffer_tokens = 1000
        min_max_output_tokens = 1024
        max_input_budget = (
            reserved_points * tokens_per_point
            - budget_buffer_tokens
            - min_max_output_tokens * multiplier_output
        )
        assert max_input_budget == 15928

        result_payload, output_tokens, compaction_audit, context_too_large = prepare_prompt_payload(
            large_payload,
            reserved_points=reserved_points,
            tokens_per_point=tokens_per_point,
            multiplier_output=multiplier_output,
            budget_buffer_tokens=budget_buffer_tokens,
            default_max_output_tokens=4096,
            min_max_output_tokens=min_max_output_tokens,
        )

        result_tokens = estimate_token_count(result_payload)
        assert result_tokens <= max_input_budget, (
            f"Result {result_tokens} exceeds real input budget {max_input_budget}"
        )
        assert result_tokens < original_tokens, "Payload should have been compacted"
        assert output_tokens >= 1024
        assert len(compaction_audit) > 0
        assert context_too_large is False

    def test_prepare_prompt_payload_no_compaction_when_within_budget(self) -> None:
        small_payload = {
            "history": [{"role": "user", "content_md": "Hello"}],
            "record_context": {"source_excerpt": "Short text"},
        }

        result_payload, output_tokens, compaction_audit, context_too_large = prepare_prompt_payload(
            small_payload,
            reserved_points=100,
            tokens_per_point=200,
            multiplier_output=3,
            budget_buffer_tokens=1000,
            default_max_output_tokens=4096,
            min_max_output_tokens=1024,
        )

        assert result_payload["history"] == small_payload["history"]
        assert result_payload["record_context"] == small_payload["record_context"]
        assert compaction_audit == []
        assert context_too_large is False

    def test_prepare_prompt_payload_low_budget_no_floor_inflation(self) -> None:
        large_payload = {
            "external_asset_contexts": [
                {"content_md": "x" * 4000} for _ in range(3)
            ],
            "record_assets": [{"title": f"A{i}", "content": "y" * 3000} for i in range(3)],
            "history": [{"role": "user", "content_md": "q" * 1000} for _ in range(5)],
            "record_context": {
                "sentence_windows": [{"text": "s" * 1500} for _ in range(4)],
                "source_excerpt": "t" * 20000,
            },
            "article_overview": "o" * 15000,
        }

        original_tokens = estimate_token_count(large_payload)
        assert original_tokens > 15000

        reserved_points = 30
        tokens_per_point = 200
        multiplier_output = 3
        budget_buffer_tokens = 500
        min_max_output_tokens = 1024
        max_input_budget = (
            reserved_points * tokens_per_point
            - budget_buffer_tokens
            - min_max_output_tokens * multiplier_output
        )
        assert max_input_budget == 2428
        assert max_input_budget < 8000

        result_payload, output_tokens, compaction_audit, context_too_large = prepare_prompt_payload(
            large_payload,
            reserved_points=reserved_points,
            tokens_per_point=tokens_per_point,
            multiplier_output=multiplier_output,
            budget_buffer_tokens=budget_buffer_tokens,
            default_max_output_tokens=4096,
            min_max_output_tokens=min_max_output_tokens,
        )

        result_tokens = estimate_token_count(result_payload)
        assert result_tokens <= max_input_budget, (
            f"Result {result_tokens} exceeds real input budget {max_input_budget} "
            f"(would have been inflated to 8000 with old floor)"
        )
        assert result_tokens < original_tokens
        assert len(compaction_audit) > 0
        assert context_too_large is False


class TestContextCompressionUxContract:
    """P0-6: Context Compression UX Contract."""

    def test_compaction_audit_records_applied_layers(self) -> None:
        large_payload = {
            "external_asset_contexts": [
                {"content_md": "x" * 4000} for _ in range(5)
            ],
            "record_assets": [{"title": f"A{i}", "content": "y" * 3000} for i in range(5)],
            "vocabulary_items": [{"word": f"w{i}", "definition": "z" * 2000} for i in range(5)],
            "record_insights": [{"insight": "i" * 2000} for _ in range(5)],
            "history": [{"role": "user", "content_md": "q" * 1000} for _ in range(10)],
            "record_context": {
                "sentence_windows": [{"text": "s" * 1500} for _ in range(8)],
                "source_excerpt": "t" * 40000,
            },
            "article_overview": "o" * 20000,
        }

        _, _, compaction_audit, context_too_large = prepare_prompt_payload(
            large_payload,
            reserved_points=100,
            tokens_per_point=200,
            multiplier_output=3,
            budget_buffer_tokens=1000,
            default_max_output_tokens=4096,
            min_max_output_tokens=1024,
        )

        assert isinstance(compaction_audit, list)
        assert len(compaction_audit) > 0
        assert all(isinstance(name, str) for name in compaction_audit)
        known_layers = {
            "external_assets", "record_assets", "vocabulary", "insights",
            "history", "sentence_windows", "source_excerpt", "article_overview",
            "external_assets_drop", "record_assets_drop", "vocabulary_drop",
            "insights_drop", "history_aggressive", "sentence_windows_drop",
            "source_excerpt_aggressive", "article_overview_aggressive",
        }
        for name in compaction_audit:
            assert name in known_layers, f"Unknown compaction layer: {name}"
        assert context_too_large is False

    def test_no_compaction_audit_when_within_budget(self) -> None:
        small_payload = {
            "history": [{"role": "user", "content_md": "Hi"}],
        }

        _, _, compaction_audit, context_too_large = prepare_prompt_payload(
            small_payload,
            reserved_points=100,
            tokens_per_point=200,
            multiplier_output=3,
            budget_buffer_tokens=1000,
            default_max_output_tokens=4096,
            min_max_output_tokens=1024,
        )

        assert compaction_audit == []
        assert context_too_large is False

    def test_context_too_large_when_budget_exceeded_after_compaction(self) -> None:
        huge_payload = {
            "history": [
                {"role": "user", "content_md": "q" * 5000} for _ in range(20)
            ],
            "record_context": {
                "source_excerpt": "t" * 50000,
            },
            "article_overview": "o" * 40000,
        }

        _, _, compaction_audit, context_too_large = prepare_prompt_payload(
            huge_payload,
            reserved_points=10,
            tokens_per_point=100,
            multiplier_output=3,
            budget_buffer_tokens=100,
            default_max_output_tokens=1024,
            min_max_output_tokens=256,
        )

        assert context_too_large is True
        assert len(compaction_audit) > 0

    def test_context_too_large_when_attachments_lost(self) -> None:
        payload_with_attachments = {
            "canonical_context": {
                "attachments": [
                    {"kind": "text_selection", "label": "选中的句子"},
                    {"kind": "record_ref", "label": "相关文章"},
                ],
            },
            "history": [{"role": "user", "content_md": "q" * 8000} for _ in range(10)],
            "record_context": {
                "source_excerpt": "t" * 30000,
            },
            "article_overview": "o" * 20000,
        }

        result_payload, _, compaction_audit, context_too_large = prepare_prompt_payload(
            payload_with_attachments,
            reserved_points=50,
            tokens_per_point=200,
            multiplier_output=3,
            budget_buffer_tokens=500,
            default_max_output_tokens=4096,
            min_max_output_tokens=512,
        )

        result_attachments = result_payload.get("canonical_context", {}).get("attachments", [])
        if len(result_attachments) < 2:
            assert context_too_large is True
        else:
            assert context_too_large is False

    def test_attachments_preserved_when_within_budget(self) -> None:
        payload_with_attachments = {
            "canonical_context": {
                "attachments": [
                    {"kind": "text_selection", "label": "选中的句子"},
                ],
            },
            "history": [{"role": "user", "content_md": "Hello"}],
        }

        result_payload, _, compaction_audit, context_too_large = prepare_prompt_payload(
            payload_with_attachments,
            reserved_points=100,
            tokens_per_point=200,
            multiplier_output=3,
            budget_buffer_tokens=1000,
            default_max_output_tokens=4096,
            min_max_output_tokens=1024,
        )

        result_attachments = result_payload.get("canonical_context", {}).get("attachments", [])
        assert len(result_attachments) == 1
        assert result_attachments[0]["kind"] == "text_selection"
        assert compaction_audit == []
        assert context_too_large is False
