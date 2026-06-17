"""Tests for reader_ask stream_events: SSE encoding, event names, and payload builders."""

from __future__ import annotations

import json

from app.services.reader_ask.stream_events import (
    EVENT_CONTEXT_COMPACTING,
    EVENT_ERROR,
    EVENT_MESSAGE_COMPLETED,
    EVENT_MESSAGE_DELTA,
    EVENT_MESSAGE_INTERRUPTED,
    EVENT_MESSAGE_STARTED,
    EVENT_REASONING_COMPLETED,
    EVENT_REASONING_DELTA,
    EVENT_REASONING_STARTED,
    EVENT_REPLAN_STARTED,
    EVENT_THREAD_READY,
    EVENT_TOOL_COMPLETED,
    EVENT_TOOL_FAILED,
    EVENT_TOOL_STARTED,
    context_compacting_payload,
    context_too_large_payload,
    encode_sse,
    http_exception_payload,
    insufficient_credits_payload,
    message_delta_payload,
    message_interrupted_payload,
    message_started_payload,
    model_unavailable_payload,
    reader_ask_failed_payload,
    reasoning_completed_payload,
    reasoning_delta_payload,
    reasoning_started_payload,
    replan_started_payload,
    thread_ready_payload,
    tool_completed_payload,
    tool_failed_payload,
    tool_started_payload,
)


class TestEncodeSse:
    """SSE wire format encoding."""

    def test_basic_encoding(self) -> None:
        result = encode_sse("message.delta", {"message_id": "abc", "delta": "hello"})
        assert result.startswith("event: message.delta\ndata: ")
        assert result.endswith("\n\n")
        # Extract and parse the data portion
        data_str = result.split("data: ", 1)[1].rstrip("\n")
        data = json.loads(data_str)
        assert data["message_id"] == "abc"
        assert data["delta"] == "hello"

    def test_cjk_content_encoded_correctly(self) -> None:
        result = encode_sse("message.delta", {"delta": "你好世界"})
        data_str = result.split("data: ", 1)[1].rstrip("\n")
        data = json.loads(data_str)
        assert data["delta"] == "你好世界"

    def test_ensure_ascii_false(self) -> None:
        """CJK characters should not be escaped to \\uXXXX sequences."""
        result = encode_sse("message.delta", {"delta": "你好"})
        assert "\\u" not in result.split("data: ", 1)[1]

    def test_empty_data(self) -> None:
        result = encode_sse("error", {})
        assert "event: error\ndata: {}\n\n" == result


class TestEventNameConstants:
    """Event name constants match the frontend ReaderAskStreamEventName type."""

    def test_all_event_names_defined(self) -> None:
        expected = {
            "thread.ready",
            "message.started",
            "message.delta",
            "message.completed",
            "message.interrupted",
            "reasoning.started",
            "reasoning.delta",
            "reasoning.completed",
            "tool.started",
            "tool.completed",
            "tool.failed",
            "context.compacting",
            "replan.started",
            "error",
        }
        actual = {
            EVENT_THREAD_READY,
            EVENT_MESSAGE_STARTED,
            EVENT_MESSAGE_DELTA,
            EVENT_MESSAGE_COMPLETED,
            EVENT_MESSAGE_INTERRUPTED,
            EVENT_REASONING_STARTED,
            EVENT_REASONING_DELTA,
            EVENT_REASONING_COMPLETED,
            EVENT_TOOL_STARTED,
            EVENT_TOOL_COMPLETED,
            EVENT_TOOL_FAILED,
            EVENT_CONTEXT_COMPACTING,
            EVENT_REPLAN_STARTED,
            EVENT_ERROR,
        }
        assert actual == expected


class TestErrorPayloadBuilders:
    """Error payload shapes match the frontend error handling contract."""

    def test_insufficient_credits_payload(self) -> None:
        payload = insufficient_credits_payload(remaining_points=5, required_points=10)
        assert payload["code"] == "INSUFFICIENT_CREDITS"
        assert payload["remaining_points"] == 5
        assert payload["required_points"] == 10
        assert "detail" in payload
        assert "user_message" in payload
        assert "本轮请求未发送给模型" in payload["user_message"]

    def test_context_too_large_payload(self) -> None:
        payload = context_too_large_payload()
        assert payload["code"] == "CONTEXT_TOO_LARGE"
        assert "detail" in payload
        assert "user_message" in payload
        assert "上下文过长" in payload["user_message"]

    def test_model_unavailable_payload(self) -> None:
        payload = model_unavailable_payload()
        assert payload["code"] == "MODEL_UNAVAILABLE"
        assert "detail" in payload

    def test_reader_ask_failed_payload(self) -> None:
        payload = reader_ask_failed_payload("something went wrong")
        assert payload["code"] == "READER_ASK_FAILED"
        assert payload["detail"] == "something went wrong"

    def test_http_exception_payload(self) -> None:
        payload = http_exception_payload(429, "Rate limited")
        assert payload["code"] == "429"
        assert payload["detail"] == "Rate limited"


class TestEventPayloadBuilders:
    """Event payload shapes match the frontend SSE consumer expectations."""

    def test_thread_ready_payload(self) -> None:
        payload = thread_ready_payload("thread-1", "record-1")
        assert payload == {"thread_id": "thread-1", "record_id": "record-1"}

    def test_message_started_payload(self) -> None:
        payload = message_started_payload("msg-1", "user-msg-1")
        assert payload == {"message_id": "msg-1", "reply_to": "user-msg-1"}

    def test_message_delta_payload(self) -> None:
        payload = message_delta_payload("msg-1", "hello")
        assert payload == {"message_id": "msg-1", "delta": "hello"}

    def test_context_compacting_payload(self) -> None:
        payload = context_compacting_payload("msg-1")
        assert payload == {"message_id": "msg-1"}

    def test_replan_started_payload(self) -> None:
        payload = replan_started_payload("msg-1", "degenerate_answer")
        assert payload == {"message_id": "msg-1", "reason": "degenerate_answer"}

    def test_reasoning_started_payload(self) -> None:
        payload = reasoning_started_payload("msg-1")
        assert payload == {"message_id": "msg-1"}

    def test_reasoning_delta_payload(self) -> None:
        payload = reasoning_delta_payload("msg-1", "thinking...")
        assert payload == {"message_id": "msg-1", "delta": "thinking..."}

    def test_reasoning_completed_payload(self) -> None:
        payload = reasoning_completed_payload("msg-1")
        assert payload == {"message_id": "msg-1"}

    def test_tool_started_payload(self) -> None:
        payload = tool_started_payload("generate_sentence_annotation")
        assert payload == {"tool_name": "generate_sentence_annotation"}

    def test_tool_completed_payload(self) -> None:
        payload = tool_completed_payload("generate_sentence_annotation", "done")
        assert payload == {"tool_name": "generate_sentence_annotation", "summary": "done"}

    def test_tool_failed_payload(self) -> None:
        payload = tool_failed_payload("generate_sentence_annotation", "Tool call limit exceeded")
        assert payload == {"tool_name": "generate_sentence_annotation", "detail": "Tool call limit exceeded"}

    def test_message_interrupted_payload(self) -> None:
        payload = message_interrupted_payload(
            message_id="msg-1",
            content_md="partial content",
            detail="输出中断",
        )
        assert payload["message_id"] == "msg-1"
        assert payload["content_md"] == "partial content"
        assert payload["detail"] == "输出中断"
        assert payload["can_retry"] is True


class TestSseRoundTrip:
    """Verify that encode_sse produces output the frontend can parse."""

    def test_context_compacting_round_trip(self) -> None:
        """The context.compacting event must be parseable by the frontend SSE consumer."""
        frame = encode_sse(EVENT_CONTEXT_COMPACTING, context_compacting_payload("msg-123"))
        # Frontend expects: event: context.compacting\ndata: {...}\n\n
        lines = frame.strip().split("\n")
        assert lines[0] == "event: context.compacting"
        data = json.loads(lines[1].removeprefix("data: "))
        assert data["message_id"] == "msg-123"

    def test_message_completed_round_trip(self) -> None:
        """The message.completed event with a full payload must be parseable."""
        completed_data = {
            "id": "msg-1",
            "thread_id": "thread-1",
            "content_md": "Hello",
            "citations": [],
            "action_proposals": [],
            "tool_trace": [],
            "evidence": [],
            "response_cards": [],
            "billed_points": 5,
            "resolved_context": {"record_id": "r1", "anchor_count": 0, "explicit_attachment_count": 0, "used_cross_record_context": False, "current_sentence_used": False, "current_paragraph_used": False, "used_record_insights": False, "used_dictionary": False, "source_labels": []},
            "supplement_candidates": [],
            "persisted_supplements": [],
        }
        frame = encode_sse(EVENT_MESSAGE_COMPLETED, completed_data)
        lines = frame.strip().split("\n")
        assert lines[0] == "event: message.completed"
        data = json.loads(lines[1].removeprefix("data: "))
        assert data["id"] == "msg-1"
        assert data["billed_points"] == 5

    def test_error_context_too_large_round_trip(self) -> None:
        """The CONTEXT_TOO_LARGE error event must be parseable."""
        frame = encode_sse(EVENT_ERROR, context_too_large_payload())
        lines = frame.strip().split("\n")
        assert lines[0] == "event: error"
        data = json.loads(lines[1].removeprefix("data: "))
        assert data["code"] == "CONTEXT_TOO_LARGE"
        assert "user_message" in data


class TestUuidToStrConversion:
    """Payload builders must coerce UUID inputs to strings for JSON serialization."""

    def test_context_compacting_payload_with_uuid(self) -> None:
        """Reproduces the retry_thread_message NameError: message_id is UUID."""
        from uuid import uuid4

        uid = uuid4()
        payload = context_compacting_payload(uid)
        assert payload["message_id"] == str(uid)
        # Verify JSON-serializable
        json.dumps(payload)

    def test_thread_ready_payload_with_uuid(self) -> None:
        from uuid import uuid4

        tid, rid = uuid4(), uuid4()
        payload = thread_ready_payload(tid, rid)
        assert payload["thread_id"] == str(tid)
        assert payload["record_id"] == str(rid)

    def test_message_started_payload_with_uuid(self) -> None:
        from uuid import uuid4

        mid, rid = uuid4(), uuid4()
        payload = message_started_payload(mid, rid)
        assert payload["message_id"] == str(mid)
        assert payload["reply_to"] == str(rid)

    def test_message_interrupted_payload_with_uuid(self) -> None:
        from uuid import uuid4

        mid = uuid4()
        payload = message_interrupted_payload(mid, "partial", "interrupted")
        assert payload["message_id"] == str(mid)

    def test_context_compacting_round_trip_with_uuid(self) -> None:
        """Full SSE round-trip with a UUID message_id (retry path)."""
        from uuid import uuid4

        uid = uuid4()
        frame = encode_sse(EVENT_CONTEXT_COMPACTING, context_compacting_payload(uid))
        lines = frame.strip().split("\n")
        data = json.loads(lines[1].removeprefix("data: "))
        assert data["message_id"] == str(uid)
