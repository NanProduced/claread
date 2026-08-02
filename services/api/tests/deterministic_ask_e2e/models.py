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
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

__all__ = [
    "DETERMINISTIC_ARTICLE_ANSWER",
    "DETERMINISTIC_GENERAL_ANSWER",
    "DETERMINISTIC_MARKER",
    "DeterministicModelMissingEvidenceError",
    "FIXTURE_ARTICLE_TEXT",
    "FIXTURE_ARTICLE_TITLE",
    "FIXTURE_QUESTION",
    "build_deterministic_ask_model",
    "deterministic_ask_model_fn",
]

HANDLE_PATTERN = re.compile(r"evh_[0-9a-f]{32}")

DETERMINISTIC_MARKER = "deterministic-e2e-r0"

DETERMINISTIC_ARTICLE_ANSWER = (
    "【deterministic-e2e-r0·article】根据本文，Riverside Library 由 Maria Chen "
    "于 1998 年创立，最初只是茶馆楼上的单间阅览室。"
)
DETERMINISTIC_GENERAL_ANSWER = (
    "【deterministic-e2e-r0·general】补充通用知识：社区图书馆通常依靠志愿者与地方资助运营。"
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


def deterministic_ask_model_fn(messages: Any, info: Any) -> ModelResponse:
    """One-shot ``final_result`` grounded in visible server handles."""
    del info
    prompt_text = _extract_prompt_text(messages)
    handles = list(dict.fromkeys(HANDLE_PATTERN.findall(prompt_text)))
    if not handles:
        raise DeterministicModelMissingEvidenceError(
            "deterministic Ask model saw no server-minted evh_ handle in "
            "the assembled prompt; refusing to emit an article block "
            f"(prompt_chars={len(prompt_text)})"
        )
    payload = {
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
    return ModelResponse(
        parts=[
            ToolCallPart(
                tool_name="final_result",
                args=json.dumps(payload, ensure_ascii=False),
                tool_call_id="deterministic-e2e-r0-final",
            )
        ]
    )


def build_deterministic_ask_model() -> FunctionModel:
    return FunctionModel(deterministic_ask_model_fn)
