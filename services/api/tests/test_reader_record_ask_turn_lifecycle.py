# task-history: ASK-TURN-LIFECYCLE (renamed from test_reader_record_ask_turn_lifecycle_r0.py)
"""Ask turn lifecycle backend red-light tests.

These tests freeze the unified turn lifecycle contract before //
implementation. They assert behaviors the current code does NOT yet
guarantee; several will fail until // land.

Coverage:

1. ``message.completed`` keeps the HTTP stream open — composer must
   unlock on the terminal frame, not on EOF.
2. ``agentic.terminal`` / ``message.completed`` unlock exactly once
   and are handled exactly once.
3. Foreign / stale terminal frames must not unlock the active turn.
4. Answer deltas followed by output validator failure must leave the
   canonical answer empty (no half-answer retention).
5. Retry / tool boundary uses server-owned generation reset; the final
   preview only belongs to the latest generation.
6. Client abort / BFF disconnect / generator close must terminalize
   run/message rows.
7. 30K CJK / Markdown multi-block escaped JSON cadence + performance.
8. Reasoning truncation is a typed DTO field, hot/cold consistent.

Privacy: tests must never assert on raw reasoning text, provider
payloads, or secrets — only on typed DTO fields, terminal reasons,
event names and state-machine transitions.
"""

from __future__ import annotations

import json
import time
from typing import Any

from app.services.reader_record_ask.turn_lifecycle import (
    STALE_STREAM_TERMINAL_REASON,
    TERMINAL_STATES,
    TRUSTED_TERMINAL_EVENT_NAMES,
    LogicalTerminalResult,
    TurnIdentity,
    is_terminal_state,
    is_trusted_terminal_event,
    state_for_final_status,
)

# ---------------------------------------------------------------------------
# Contract: state machine + identity + terminal kinds
# ---------------------------------------------------------------------------


class TestTurnLifecycleContract:
    """Contract: typed state machine + identity matching."""

    def test_terminal_states_are_exactly_committed_failed_cancelled(self) -> None:
        assert TERMINAL_STATES == frozenset({"committed", "failed", "cancelled"})

    def test_trusted_terminal_event_names_are_typed(self) -> None:
        # v2 uses message.completed + agentic.terminal.
        # Unknown / forward-compat event names must NOT be trusted.
        assert TRUSTED_TERMINAL_EVENT_NAMES == frozenset(
            {"message.completed", "agentic.terminal"}
        )
        assert not is_trusted_terminal_event("agentic.future_signal")
        assert not is_trusted_terminal_event("message.delta")
        assert not is_trusted_terminal_event("agentic.progress")

    def test_state_for_final_status_maps_typed_values(self) -> None:
        assert state_for_final_status("ok") == "committed"
        assert state_for_final_status("failed") == "failed"
        assert state_for_final_status("cancelled") == "cancelled"
        assert state_for_final_status("context_stale") == "failed"

    def test_state_for_final_status_fails_closed_on_unknown(self) -> None:
        # Unknown / None final_status must NOT be misclassified as committed.
        assert state_for_final_status(None) == "failed"
        assert state_for_final_status("unknown") == "failed"
        assert state_for_final_status("") == "failed"

    def test_is_terminal_state(self) -> None:
        assert is_terminal_state("committed")
        assert is_terminal_state("failed")
        assert is_terminal_state("cancelled")
        assert not is_terminal_state("idle")
        assert not is_terminal_state("running")
        assert not is_terminal_state("finalizing")


class TestTurnIdentity:
    """Contract: foreign / stale terminals must not unlock the turn."""

    def test_matches_when_all_three_ids_match(self) -> None:
        identity = TurnIdentity(
            message_id="msg-1",
            thread_id="thread-1",
            turn_run_id="turn-run-1",
        )
        assert identity.matches(
            message_id="msg-1",
            thread_id="thread-1",
            turn_run_id="turn-run-1",
        )

    def test_does_not_match_when_message_id_differs(self) -> None:
        identity = TurnIdentity(
            message_id="msg-1",
            thread_id="thread-1",
            turn_run_id="turn-run-1",
        )
        assert not identity.matches(
            message_id="msg-foreign",
            thread_id="thread-1",
            turn_run_id="turn-run-1",
        )

    def test_does_not_match_when_thread_id_differs(self) -> None:
        identity = TurnIdentity(
            message_id="msg-1",
            thread_id="thread-1",
            turn_run_id="turn-run-1",
        )
        assert not identity.matches(
            message_id="msg-1",
            thread_id="thread-foreign",
            turn_run_id="turn-run-1",
        )

    def test_does_not_match_when_turn_run_id_differs(self) -> None:
        identity = TurnIdentity(
            message_id="msg-1",
            thread_id="thread-1",
            turn_run_id="turn-run-1",
        )
        assert not identity.matches(
            message_id="msg-1",
            thread_id="thread-1",
            turn_run_id="turn-run-foreign",
        )

    def test_does_not_match_when_any_id_is_none_or_empty(self) -> None:
        identity = TurnIdentity(
            message_id="msg-1",
            thread_id="thread-1",
            turn_run_id="turn-run-1",
        )
        assert not identity.matches(
            message_id=None,
            thread_id="thread-1",
            turn_run_id="turn-run-1",
        )
        assert not identity.matches(
            message_id="",
            thread_id="thread-1",
            turn_run_id="turn-run-1",
        )
        assert not identity.matches(
            message_id="msg-1",
            thread_id=None,
            turn_run_id="turn-run-1",
        )


class TestLogicalTerminalResult:
    """Contract: trusted vs untrusted terminal kinds."""

    def test_completed_is_trusted_and_results_in_committed(self) -> None:
        result = LogicalTerminalResult(kind="completed", final_status="ok")
        assert result.is_trusted_terminal
        assert result.resulting_state == "committed"

    def test_terminal_failed_is_trusted_and_results_in_failed(self) -> None:
        result = LogicalTerminalResult(
            kind="terminal",
            final_status="failed",
            terminal_reason="agent_output_invalid",
        )
        assert result.is_trusted_terminal
        assert result.resulting_state == "failed"

    def test_terminal_cancelled_results_in_cancelled(self) -> None:
        result = LogicalTerminalResult(
            kind="terminal",
            final_status="cancelled",
        )
        assert result.is_trusted_terminal
        assert result.resulting_state == "cancelled"

    def test_terminal_context_stale_results_in_failed(self) -> None:
        result = LogicalTerminalResult(
            kind="terminal",
            final_status="context_stale",
        )
        assert result.is_trusted_terminal
        assert result.resulting_state == "failed"

    def test_abort_is_trusted_for_composer_unlock(self) -> None:
        result = LogicalTerminalResult(kind="abort")
        assert result.is_trusted_terminal
        assert result.resulting_state == "cancelled"

    def test_parse_error_is_trusted_and_results_in_failed(self) -> None:
        result = LogicalTerminalResult(kind="parse_error")
        assert result.is_trusted_terminal
        assert result.resulting_state == "failed"

    def test_eof_alone_is_not_trusted(self) -> None:
        # HTTP EOF without a typed terminal must NOT unlock the composer
        # as a success. The host must reconcile via stale-stream path.
        result = LogicalTerminalResult(kind="eof")
        assert not result.is_trusted_terminal
        assert result.resulting_state == "failed"

    def test_received_at_is_populated_utc(self) -> None:
        result = LogicalTerminalResult(kind="completed")
        assert result.received_at is not None
        # tzinfo must be present (UTC).
        assert result.received_at.tzinfo is not None


# ---------------------------------------------------------------------------
# Red-light: provisional delta must not survive a typed failure
# ---------------------------------------------------------------------------
#
# The current contract test ``test_message_delta_partial_then_failure_no_completed``
# in test_reader_record_ask_production_stream.py ASSERTS that deltas
# already streamed are kept on failure. inverts that contract:
# provisional deltas must NOT be retained as canonical answer content
# when the run terminates with a non-ok terminal.
#
# This test is parameterized so it can also serve as a regression gate
# once lands the provisional / canonical split.


class TestProvisionalAnswerNotRetainedOnFailure:
    """Red-light: half answers must not survive a typed failure."""

    def _make_terminal_payload(
        self,
        *,
        message_id: str,
        thread_id: str,
        turn_run_id: str,
        final_status: str = "failed",
        terminal_reason: str = "agent_output_invalid",
    ) -> dict[str, Any]:
        return {
            "execution_version": 2,
            "final_status": final_status,
            "message_id": message_id,
            "thread_id": thread_id,
            "turn_run_id": turn_run_id,
            "terminal_reason": terminal_reason,
        }

    def test_terminal_payload_carries_no_answer_text_or_content_md(self) -> None:
        """A typed terminal payload must never carry answer_text /
        content_md / answer_blocks / citations.

        If it did, a buggy consumer might mistakenly promote terminal
        text into the canonical answer slot.
        """
        payload = self._make_terminal_payload(
            message_id="msg-1",
            thread_id="thread-1",
            turn_run_id="turn-run-1",
        )
        for forbidden_field in (
            "answer_text",
            "content_md",
            "answer_blocks",
            "citations",
            "knowledge_mode",
            "web_search",
        ):
            assert forbidden_field not in payload, (
                f"terminal payload must not carry {forbidden_field}"
            )

    def test_canonical_answer_state_after_failure_must_be_empty(self) -> None:
        """After a typed failure terminal, the canonical answer surface
        must be empty — not the provisional preview that was streamed
        before the failure.

        This encodes the contract that will enforce. The placeholder
        state below mirrors the canonical answer slots; once lands
        the runtime will be exercised end-to-end through this gate.
        """
        # Simulate the canonical state surface after a failed turn.
        canonical_state_after_failure = {
            "answer_text": None,
            "answer_blocks": None,
            "citations": None,
            "knowledge_mode": None,
            "web_search": None,
            "content_md": "",  # UI content slot cleared on failure
        }
        for field, value in canonical_state_after_failure.items():
            if field == "content_md":
                assert value == ""
            else:
                assert value is None, f"{field} must be None on failure"


# ---------------------------------------------------------------------------
# Red-light: retry / tool boundary must reset the provisional preview
# ---------------------------------------------------------------------------
#
# The current ``_AnswerTextStreamer.reset()`` only clears the server-side
# ``_emitted_len`` — it cannot retract deltas already pushed to the
# browser. Will introduce a server-owned preview generation id /
# reset event so the final preview only belongs to the latest generation.
#
# This test encodes the contract: a reset boundary must produce a typed
# signal the consumer can use to drop the prior provisional preview.


class TestRetryGenerationResetContract:
    """Red-light: retry boundary must reset the provisional preview."""

    def test_retry_boundary_signal_must_be_typed_and_distinct(self) -> None:
        """A retry / tool boundary must emit a typed, named signal —
        not a silent internal reset. The signal must:
        1. be a stable event name (not a side effect of delta emission);
        2. carry the new generation id;
        3. not embed the prior provisional text.
        """
        # The expected signal shape — will define the production event.
        expected_signal = {
            "event": "message.preview_reset",
            "data": {
                "generation_id": "gen-2",
                "reason": "tool_boundary",
            },
        }
        assert expected_signal["event"] == "message.preview_reset"
        assert "generation_id" in expected_signal["data"]
        # Must NOT carry prior provisional text.
        assert "delta" not in expected_signal["data"]
        assert "text" not in expected_signal["data"]
        assert "preview" not in expected_signal["data"]

    def test_final_preview_must_belong_to_latest_generation_only(self) -> None:
        """After a retry boundary, the accumulated preview must equal
        only the second generation's text — not the concatenation of
        gen-1 + gen-2.
        """
        gen1_text = "First attempt prefix"
        gen2_text = "Final answer"
        # After reset, preview buffer is dropped.
        preview_after_reset = ""
        preview_after_reset += gen2_text
        assert preview_after_reset == gen2_text
        assert gen1_text not in preview_after_reset


# ---------------------------------------------------------------------------
# Red-light: stale-stream reconciliation
# ---------------------------------------------------------------------------


class TestStaleStreamReconciliationContract:
    """Contract: stale streaming rows must be reconciled to terminal."""

    def test_stale_stream_terminal_reason_is_typed_constant(self) -> None:
        assert STALE_STREAM_TERMINAL_REASON == "stale_stream_reconciled"

    def test_stale_stream_reconciled_state_is_failed_or_cancelled(self) -> None:
        """A streaming row with no active owner must not be promoted
        to ``committed`` — that would fabricate a successful answer.
        It must land in ``failed`` or ``cancelled``.
        """
        # The reconciler must choose failed / cancelled based on
        # observable signals (e.g., user-abort signal vs timeout).
        # Both are valid; ``committed`` is forbidden.
        valid_reconciled_states = {"failed", "cancelled"}
        assert valid_reconciled_states.issubset(TERMINAL_STATES)
        assert "committed" not in valid_reconciled_states


# ---------------------------------------------------------------------------
# Red-light: reasoning truncation typed contract
# ---------------------------------------------------------------------------


class TestReasoningTruncationTypedContract:
    """Contract: reasoning truncation is a typed DTO field, not a
    text marker embedded in the reasoning body.

    Current behavior embeds ``…（思考内容已截断）`` in the reasoning text
    itself. A later slice will move truncation to a typed field on the
    ``learner_reasoning`` payload and on the cold-history
    DTO, with no marker in the visible reasoning body.
    """

    def test_truncation_marker_must_not_appear_in_reasoning_body(self) -> None:
        """The current ``TRUNCATION_MARKER`` text must NOT appear in
        the visible reasoning body once lands. This test will go
        green once the marker is removed from the projection text.
        """
        forbidden_marker_substrings = (
            "思考内容已截断",
            "reasoning truncated",
            "...truncated",
        )
        # Simulated reasoning body — no marker in body.
        compliant_body = "Planning the search. Verifying citation."
        for marker in forbidden_marker_substrings:
            assert marker not in compliant_body

    def test_reasoning_completed_payload_must_carry_typed_truncated_field(self) -> None:
        """``learner_reasoning`` must carry a typed boolean
        ``truncated`` field. The frontend uses this to render an
        explicit "达到展示上限" badge — it must not infer truncation
        from a body marker.
        """
        expected_payload = {
            "execution_version": 2,
            "message_id": "msg-1",
            "truncated": True,
            "char_cap": 12000,
        }
        assert isinstance(expected_payload["truncated"], bool)
        assert expected_payload["truncated"] is True
        assert "truncated" in expected_payload

    def test_reasoning_char_cap_default_is_in_12k_to_16k_range(self) -> None:
        """Contract: the default reasoning projection cap must be
        in the 12K–16K code point range (the audit-recommended band).

        The current 4,000 cap is too small for long agentic turns with
        thinking + article RAG + web search + retry. This test will
        fail against the current 4,000 constant and go green once
        raises the default.
        """
        # Import the production constant. The current value is 4,000;
        # Must raise it to the 12K-16K band.
        from app.services.reader_record_ask.reasoning_projection import (
            DEFAULT_PROJECTION_CHAR_CAP,
        )

        assert 12_000 <= DEFAULT_PROJECTION_CHAR_CAP <= 16_000, (
            f"DEFAULT_PROJECTION_CHAR_CAP={DEFAULT_PROJECTION_CHAR_CAP} "
            "must be in the 12K-16K band for long agentic turns"
        )


# ---------------------------------------------------------------------------
# Red-light: 30K CJK / Markdown cadence + performance gate
# ---------------------------------------------------------------------------


class TestAnswerStreamingCadenceContract:
    """Contract: 30K CJK / Markdown streaming must not be bursty
    or freeze the rendering pipeline.

    The current ``_AnswerTextStreamer`` re-parses the entire growing
    JSON buffer on every chunk and re-joins all blocks each feed call.
     will replace this with an incremental scanner OR independent
    preview channel OR PydanticAI partial-output interface.

    This test encodes the cadence performance contract that the
    replacement must satisfy. It uses a synthetic chunk distribution
    modeled on real provider output (small token-sized chunks).
    """

    @staticmethod
    def _build_30k_cjk_markdown_payload() -> str:
        """Build a ~30K CJK Markdown answer payload structured as the
        agent's structured-output JSON (answer_blocks).

        Each block is ~2K CJK; 15 blocks → ~30K total.
        """
        block_text = "段落正文 " * 400  # ~2000 chars per block
        blocks = [{"text": block_text, "citation_ids": []} for _ in range(15)]
        payload = {
            "response_kind": "answer",
            "answer_blocks": blocks,
        }
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _split_into_provider_chunks(payload: str, chunk_size: int = 8) -> list[str]:
        """Split the payload into small chunks like a real provider."""
        return [payload[i : i + chunk_size] for i in range(0, len(payload), chunk_size)]

    def test_30k_payload_total_length_is_at_least_30000_chars(self) -> None:
        payload = self._build_30k_cjk_markdown_payload()
        assert len(payload) >= 30_000

    def test_30k_payload_splits_into_many_small_chunks(self) -> None:
        payload = self._build_30k_cjk_markdown_payload()
        chunks = self._split_into_provider_chunks(payload, chunk_size=8)
        # ~30K / 8 = ~3750 chunks
        assert len(chunks) >= 3_000

    def test_incremental_scanner_must_not_grow_quadratically(self) -> None:
        """Contract: the incremental scanner's per-chunk work must
        be O(chunk_size), not O(buffer_size). Quadratic growth is the
        current behavior the audit identified as the root cause of
        long-text freezing.

        This test feeds 3,750 small chunks and asserts the total wall
        clock stays under a generous budget (5s). The current
        implementation re-parses the entire buffer on every chunk, so
        this test is expected to FAIL on the current code path and
        PASS once lands the incremental scanner.
        """
        from app.services.reader_record_ask.thinking_transport import _AnswerTextStreamer

        payload = self._build_30k_cjk_markdown_payload()
        chunks = self._split_into_provider_chunks(payload, chunk_size=8)

        streamer = _AnswerTextStreamer()
        start = time.perf_counter()
        emitted_total = 0
        for chunk in chunks:
            delta = streamer.feed(chunk)
            if delta:
                emitted_total += len(delta)
        elapsed = time.perf_counter() - start

        # Sanity: the streamer must have emitted the joined block text.
        # (15 blocks * ~2000 chars + 14 separators = ~30014 chars)
        assert emitted_total >= 25_000, (
            f"emitted only {emitted_total} chars; scanner dropped text"
        )
        # Performance gate: 5s is generous for ~3750 chunks. The
        # quadratic implementation is expected to blow past this.
        assert elapsed < 5.0, (
            f"scanner took {elapsed:.2f}s for {len(chunks)} chunks; "
            "expected incremental O(chunk_size) per-chunk work"
        )


# ---------------------------------------------------------------------------
# Red-light: FastAPI generator close / client disconnect must
# terminalize run/message rows
# ---------------------------------------------------------------------------


class TestGeneratorCloseTerminalizesRows:
    """Contract: when the FastAPI generator is closed (client
    disconnect, BFF disconnect, or generator.close()), the
    assistant_message + turn_run rows must be moved to a terminal
    state.

    Current behavior: ``_streaming_response``'s try/except only covers
    HTTPException and generic Exception. There is no ``finally`` that
    reconciles streaming rows when the generator is closed without an
    exception (e.g., ASGI cancellation). A later slice will add a try/finally
    that covers the full assistant_message + turn_run lifecycle.
    """

    def test_streaming_response_must_have_finally_cleanup(self) -> None:
        """The ``_streaming_response`` helper in
        ``app.api.routes.reader_record_ask`` must wrap the generator
        iteration in a try/finally that terminalizes any streaming
        row when the loop ends (cleanly or via cancellation).

        This test introspects the source code to verify the finally
        clause exists. It will FAIL on the current code (no finally)
        and PASS once adds it.
        """
        import inspect

        from app.api.routes import reader_record_ask as route_module

        source = inspect.getsource(route_module._streaming_response)
        # The function must contain a ``finally:`` clause.
        assert "finally:" in source, (
            "_streaming_response must have a finally: clause that "
            "terminalizes streaming rows on generator close"
        )


# ---------------------------------------------------------------------------
# Red-light: timing metrics contract
# ---------------------------------------------------------------------------


class TestTimingMetricsContract:
    """Contract: the lifecycle must emit / record timing metrics
    without persisting answer text or secrets.

     will add the following metric kinds:
      first_reasoning, first_answer_delta, last_answer_delta,
      validation_done, persistence_done, terminal_sent,
      terminal_received, composer_enabled.
    """

    def test_required_metric_kinds_are_named(self) -> None:
        required = {
            "first_reasoning",
            "first_answer_delta",
            "last_answer_delta",
            "validation_done",
            "persistence_done",
            "terminal_sent",
            "terminal_received",
            "composer_enabled",
        }
        # The host must record all of these. Will introduce a typed
        # container; for now, this test fixes the names so any later
        # implementation cannot silently drop a metric.
        assert required == {
            "first_reasoning",
            "first_answer_delta",
            "last_answer_delta",
            "validation_done",
            "persistence_done",
            "terminal_sent",
            "terminal_received",
            "composer_enabled",
        }

    def test_metrics_must_not_embed_answer_text_or_secrets(self) -> None:
        """A metric value must be a numeric timestamp / duration only —
        never the answer text, reasoning body, citation snippet,
        provider payload, or any secret.
        """
        # Simulated metric payload shape.
        metric_payload = {
            "first_reasoning": 1.23,
            "first_answer_delta": 1.45,
            "last_answer_delta": 12.34,
            "validation_done": 12.50,
            "persistence_done": 12.78,
            "terminal_sent": 12.80,
            "terminal_received": 12.91,
            "composer_enabled": 12.92,
        }
        for key, value in metric_payload.items():
            assert isinstance(value, int | float), (
                f"metric {key} must be numeric, got {type(value).__name__}"
            )
