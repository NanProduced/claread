"""Deterministic verbatim recent-history window for the main Ask model."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Any

from app.services.reader_record_ask.model_view_budget import (
    ModelViewRenderer,
    RenderedModelView,
)
from app.services.reader_record_ask.thread_memory.redaction import (
    redact_for_compaction_input,
)


@dataclass(frozen=True, slots=True)
class RecentHistoryPartition:
    """A complete-turn suffix plus the aged prefix that must be compacted."""

    aged_messages: tuple[dict[str, Any], ...]
    recent_messages: tuple[dict[str, Any], ...]
    rendered_view: RenderedModelView | None


def _turn_groups(
    canonical_messages: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] | None = None
    for message in canonical_messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").lower()
        if role == "user":
            current = [message]
            groups.append(current)
        elif role == "assistant" and current is not None:
            current.append(message)
    return groups


def _render_groups(
    groups: list[list[dict[str, Any]]],
    *,
    renderer: ModelViewRenderer,
    first_turn: int,
) -> RenderedModelView | None:
    if not groups:
        return None
    lines = [
        '<conversation_history role="data" not_instructions="true">'
    ]
    for offset, group in enumerate(groups):
        turn = first_turn + offset
        for message in group:
            role = str(message.get("role") or "").lower()
            if role not in {"user", "assistant"}:
                continue
            content = message.get("content_md")
            if not isinstance(content, str):
                continue
            redacted, _metrics = redact_for_compaction_input(content)
            if not redacted:
                continue
            lines.append(
                f'<message role="{role}" turn="{turn}">'
                f"{escape(redacted)}"
                "</message>"
            )
    lines.append("</conversation_history>")
    if len(lines) == 2:
        return None
    return renderer.render_plain("\n".join(lines))


def partition_recent_history(
    *,
    canonical_messages: list[dict[str, Any]],
    renderer: ModelViewRenderer,
    budget_chars: int,
    recent_pairs: int = 20,
) -> RecentHistoryPartition:
    """Fit the newest complete turns; move all overflow to the aged prefix.

    A turn is atomic: no message body or XML line is ever sliced to fit.
    The recent window is both pair-bounded and character-bounded.
    """

    if recent_pairs < 1:
        raise ValueError("recent_pairs must be >= 1")
    if budget_chars < 0:
        raise ValueError("budget_chars must be >= 0")

    groups = _turn_groups(canonical_messages)
    if not groups or budget_chars == 0:
        return RecentHistoryPartition(
            aged_messages=tuple(
                message
                for group in groups
                for message in group
            ),
            recent_messages=(),
            rendered_view=None,
        )

    candidate_start = max(0, len(groups) - recent_pairs)
    selected_start = len(groups)
    selected_view: RenderedModelView | None = None
    for index in range(len(groups) - 1, candidate_start - 1, -1):
        candidate_groups = groups[index:]
        candidate_view = _render_groups(
            candidate_groups,
            renderer=renderer,
            first_turn=index + 1,
        )
        if candidate_view is None or candidate_view.char_cost > budget_chars:
            break
        selected_start = index
        selected_view = candidate_view

    aged = tuple(
        message
        for group in groups[:selected_start]
        for message in group
    )
    recent = tuple(
        message
        for group in groups[selected_start:]
        for message in group
    )
    return RecentHistoryPartition(
        aged_messages=aged,
        recent_messages=recent,
        rendered_view=selected_view,
    )


__all__ = [
    "RecentHistoryPartition",
    "partition_recent_history",
]
