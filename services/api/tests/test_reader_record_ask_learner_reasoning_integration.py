"""Component integration for learner-reasoning sidecar.

Manual Sidecar driving — NOT a production-stream entry test.
For real ``stream_agentic_thread_message`` coverage see
``test_reader_record_ask_learner_reasoning_production_integration.py``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.services.reader_record_ask.history_projection import (
    _safe_reasoning_projection,
)
from app.services.reader_record_ask.learner_reasoning.capacity import (
    reset_global_projector_limiter_for_tests,
)
from app.services.reader_record_ask.learner_reasoning.sidecar import (
    LearnerReasoningSidecar,
    LearnerReasoningSnapshotEvent,
)
from app.services.reader_record_ask.sse import (
    EVENT_AGENTIC_LEARNER_REASONING_SNAPSHOT,
    encode_sse,
)


@pytest.mark.asyncio
async def test_scripted_pipeline_thinking_to_cold() -> None:
    reset_global_projector_limiter_for_tests(limit=8)
    events: list[Any] = []

    async def run_fn(_w: str) -> str | None:
        await asyncio.sleep(0.02)
        return "正在整理当前思路"

    sc = LearnerReasoningSidecar(
        emit=events.append,
        message_id="msg-1",
        thread_id="thr-1",
        turn_run_id="run-1",
        run_fn=run_fn,
        enabled=True,
        finalize_grace_seconds=0.5,
    )

    # Transport-equivalent sequence
    sc.on_reasoning_delta("初步分析用户问题的核心意图与范围。")
    sc.on_reasoning_segment_end()  # CP1
    sc.on_evidence_boundary(tool_name="search_current_article")
    sc.advance_round("normal_tool_result")
    sc.on_reasoning_delta("结合检索到的段落重新组织证据。")
    sc.on_reasoning_segment_end()  # CP2
    sc.on_first_answer_delta()  # CP3 best-effort

    await sc.finalize_for_persist(grace_seconds=0.4)

    snaps = [e for e in events if isinstance(e, LearnerReasoningSnapshotEvent)]
    assert len(snaps) >= 1
    last = snaps[-1]
    wire = last.model_dump(mode="json")
    assert wire["policy_version"] == "learner_reasoning_v1"
    assert wire["message_id"] == "msg-1"
    assert wire["thread_id"] == "thr-1"
    assert wire["turn_run_id"] == "run-1"
    assert "https://" not in wire["text"]
    sse = encode_sse(EVENT_AGENTIC_LEARNER_REASONING_SNAPSHOT, wire)
    assert EVENT_AGENTIC_LEARNER_REASONING_SNAPSHOT in sse
    assert "event:" in sse

    payload = sc.persistence_payload()
    assert payload is not None
    # hot final ≡ DB
    assert payload["text"] == last.text
    assert payload["sequence"] == last.sequence

    cold_text, _, cold_stage = _safe_reasoning_projection(
        {"reasoning_projection_json": payload}
    )
    assert cold_text == payload["text"]
    assert cold_stage == payload["stage"]

    # No publish after snapshot freeze
    from app.services.reader_record_ask.learner_reasoning.schemas import (
        ValidatedLearnerSummary,
    )

    before = sc.persistence_payload()
    sc._on_summary(
        ValidatedLearnerSummary(
            text="冻结后不该出现的摘要",
            stage="synthesizing",
            basis=("general",),
            revision=99,
            sequence=99,
            generation_id=0,
        )
    )
    assert sc.persistence_payload() == before

    await sc.aclose()
    assert sc.dispatch_count <= 3
