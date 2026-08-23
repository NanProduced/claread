"""Deterministic FunctionModel producing legal Ask v2 output.

The model script never invents evidence. It scans the assembled prompt for
server-minted ``evh_`` handles (minted by the real baseline context
assembler from the real Stable Document / Anchor Segments of the fixture
record) and grounds one ``article`` block in the first handle plus one
``general`` block with no handles. If no handle is visible the model
refuses loudly — it never emits an ungrounded article block and never
falls back to a legacy/v1 answer shape.

Output shape is the canonical block form accepted by
``AgentAnswerDraftOutput`` (``response_kind="grounded_answer"`` +
``answer_blocks``); the host derives ``article_scope`` /
``knowledge_mode`` and mints public citations.

Provider reasoning streaming contract: the model is wired with a
``stream_function`` so the REAL thinking transport runs in streaming
mode. One full turn covers exactly one tool/retry generation boundary:

- Round 1: multiple ``ThinkingPartDelta`` chunks, then either a real
  ``expand_evidence`` call (when the agent mounted it — the production
  tool executes against the live stable document) or a ``final_result``
  output-tool call whose draft cites an unknown handle so the grounding
  validator raises ``ModelRetry`` (the OutputToolResultEvent retry
  boundary).
- Round 2: one ``ThinkingPartDelta`` chunk, then the final answer
  streamed as text chunks (drives ``message.delta`` answer streaming).

The deterministic reasoning text is deliberately free of URLs, handles,
UUIDs and provider wrappers so the projection publishes it verbatim
(``visibility_status="complete"``).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any

from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import (
    DeltaThinkingPart,
    DeltaToolCall,
    FunctionModel,
)
from pydantic_ai.profiles import ModelProfile

__all__ = [
    "DETERMINISTIC_ARTICLE_ANSWER",
    "DETERMINISTIC_GENERAL_ANSWER",
    "DETERMINISTIC_MARKER",
    "DETERMINISTIC_REASONING_ROUND1_CHUNKS",
    "DETERMINISTIC_REASONING_ROUND2",
    "DETERMINISTIC_REASONING_TEXT",
    "DeterministicModelMissingEvidenceError",
    "FIXTURE_ARTICLE_TEXT",
    "FIXTURE_ARTICLE_TITLE",
    "FIXTURE_QUESTION",
    "build_deterministic_ask_model",
    "deterministic_ask_model_fn",
    "deterministic_ask_stream_fn",
    "stream_delay_seconds",
]

STREAM_DELAY_ENV = "DETERMINISTIC_E2E_STREAM_DELAY_MS"


def stream_delay_seconds() -> float:
    """Tiny fixed per-yield pacing for live UI E2E (default 0 — off).

    ``DETERMINISTIC_E2E_STREAM_DELAY_MS`` gives Playwright a stable
    window to observe mid-stream reasoning UI states (thinking trigger,
    partially-arrived reasoning text). It only paces the async
    generator; it never changes the emitted content or ordering, and
    the default (unset / invalid / negative) is zero delay.
    """
    raw = os.environ.get(STREAM_DELAY_ENV, "")
    try:
        milliseconds = float(raw) if raw.strip() else 0.0
    except ValueError:
        return 0.0
    return max(milliseconds, 0.0) / 1000.0

HANDLE_PATTERN = re.compile(r"evh_[0-9a-f]{32}")

DETERMINISTIC_MARKER = "deterministic-e2e-r0"

DETERMINISTIC_ARTICLE_ANSWER = (
    "【deterministic-e2e-r0·article】根据本文，Riverside Library 由 Maria Chen "
    "于 1998 年创立，最初只是茶馆楼上的单间阅览室。"
)
DETERMINISTIC_GENERAL_ANSWER = (
    "【deterministic-e2e-r0·general】补充通用知识：社区图书馆通常依靠志愿者与地方资助运营。"
)

# Deterministic provider reasoning (safe text: no URLs, no handles, no
# identity keys — survives the projection redactor byte-identical).
DETERMINISTIC_REASONING_ROUND1_CHUNKS: tuple[str, ...] = (
    "第一步：先阅读问题，确认要找的是图书馆的创立者与最初地点。",
    "接着在文章中定位与创立相关的句子。",
)
DETERMINISTIC_REASONING_ROUND2: str = "结合工具返回的原文，整理出最终答案。"
DETERMINISTIC_REASONING_TEXT: str = "".join(DETERMINISTIC_REASONING_ROUND1_CHUNKS) + (
    DETERMINISTIC_REASONING_ROUND2
)

FIXTURE_ARTICLE_TITLE = "Riverside Library"
FIXTURE_ARTICLE_TEXT = (
    "Riverside Library was founded in 1998 by Maria Chen. "
    "It began as a single reading room above a tea shop on Harbour Street. "
    "Chen stocked the first shelves with donated novels and travel guides, "
    "and she hand-painted the opening hours on the window. "
    "By 2005 the library had expanded to three floors after a successful "
    "community fundraiser. "
    "Volunteers ran weekend English reading clubs for children and new "
    "immigrants. "
    "Today the library lends more than forty thousand books each year and "
    "hosts a small local history archive. "
    "The building overlooks the river promenade, and its reading room "
    "still keeps the original wooden desks from the tea shop era."
)
FIXTURE_QUESTION = (
    "Who founded Riverside Library and where did it begin? "
    "谁创立了 Riverside Library，它最初在哪里？"
)


class DeterministicModelMissingEvidenceError(RuntimeError):
    """Fail closed: no server-minted evidence handle was visible."""


def _extract_prompt_text(messages: Any) -> str:
    chunks: list[str] = []
    for message in messages or ():
        for part in getattr(message, "parts", ()) or ():
            content = getattr(part, "content", None)
            if isinstance(content, str):
                chunks.append(content)
            elif content is not None:
                chunks.append(str(content))
    return "\n".join(chunks)


def _visible_handles(prompt_text: str) -> list[str]:
    return list(dict.fromkeys(HANDLE_PATTERN.findall(prompt_text)))


def _has_generation_boundary(messages: Any) -> bool:
    """True once a tool result or retry prompt is in the message history."""
    for message in messages or ():
        for part in getattr(message, "parts", ()) or ():
            part_type = type(part).__name__
            if part_type in {"ToolReturnPart", "RetryPromptPart"}:
                return True
    return False


def _answer_payload(handles: list[str]) -> dict[str, Any]:
    return {
        "response_kind": "grounded_answer",
        "answer_blocks": [
            {
                "text": DETERMINISTIC_ARTICLE_ANSWER,
                "basis": "article",
                "evidence_handles": [handles[0]],
            },
            {
                "text": DETERMINISTIC_GENERAL_ANSWER,
                "basis": "general",
                "evidence_handles": [],
            },
        ],
    }


def _unknown_handle_retry_payload() -> dict[str, Any]:
    """Schema-legal draft citing an unknown handle → grounding ModelRetry."""
    return {
        "response_kind": "grounded_answer",
        "answer_blocks": [
            {
                "text": "deterministic-e2e-r0 第一稿占位",
                "basis": "article",
                "evidence_handles": ["evh_" + "0" * 32],
            }
        ],
    }


def deterministic_ask_model_fn(messages: Any, info: Any) -> ModelResponse:
    """One-shot ``final_result`` grounded in visible server handles."""
    del info
    prompt_text = _extract_prompt_text(messages)
    handles = _visible_handles(prompt_text)
    if not handles:
        raise DeterministicModelMissingEvidenceError(
            "deterministic Ask model saw no server-minted evh_ handle in "
            "the assembled prompt; refusing to emit an article block "
            f"(prompt_chars={len(prompt_text)})"
        )
    return ModelResponse(
        parts=[
            ToolCallPart(
                tool_name="final_result",
                args=json.dumps(_answer_payload(handles), ensure_ascii=False),
                tool_call_id="deterministic-e2e-r0-final",
            )
        ]
    )


async def deterministic_ask_stream_fn(messages: Any, info: Any):
    """Two-round streamed turn: thinking → tool/retry boundary → thinking
    + streamed text answer (see module docstring)."""
    prompt_text = _extract_prompt_text(messages)
    handles = _visible_handles(prompt_text)
    if not handles:
        raise DeterministicModelMissingEvidenceError(
            "deterministic Ask stream model saw no server-minted evh_ "
            "handle in the assembled prompt; refusing to emit an article "
            f"block (prompt_chars={len(prompt_text)})"
        )

    delay = stream_delay_seconds()

    if not _has_generation_boundary(messages):
        # Round 1: streamed thinking chunks, then force a generation
        # boundary via a real production tool when available.
        for chunk in DETERMINISTIC_REASONING_ROUND1_CHUNKS:
            if delay:
                await asyncio.sleep(delay)
            yield {0: DeltaThinkingPart(content=chunk)}
        function_tools = getattr(info, "function_tools", None) or ()
        tool_names = {getattr(tool, "name", None) for tool in function_tools}
        if delay:
            await asyncio.sleep(delay)
        if "expand_evidence" in tool_names:
            yield {
                1: DeltaToolCall(
                    name="expand_evidence",
                    json_args=json.dumps({"pointer": handles[0]}),
                    tool_call_id="deterministic-e2e-r0-expand",
                )
            }
        else:
            # No expand pointer this turn: force the output-validator
            # ModelRetry boundary with a schema-legal but ungrounded draft.
            yield {
                1: DeltaToolCall(
                    name="final_result",
                    json_args=json.dumps(_unknown_handle_retry_payload(), ensure_ascii=False),
                    tool_call_id="deterministic-e2e-r0-retry",
                )
            }
        return

    # Round 2: thinking chunk, then the final answer streamed as text.
    if delay:
        await asyncio.sleep(delay)
    yield {0: DeltaThinkingPart(content=DETERMINISTIC_REASONING_ROUND2)}
    payload = json.dumps(_answer_payload(handles), ensure_ascii=False)
    for i in range(0, len(payload), 24):
        if delay:
            await asyncio.sleep(delay)
        yield payload[i : i + 24]


def build_deterministic_ask_model() -> FunctionModel:
    return FunctionModel(
        function=deterministic_ask_model_fn,
        stream_function=deterministic_ask_stream_fn,
        profile=ModelProfile(supports_thinking=True),
    )
