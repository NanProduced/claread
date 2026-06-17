"""Planner Runtime: quick-action and submission-mode helpers for Ask Claread.

Round 15: the semantic planner execution path (``run_semantic_planner``,
``resolve_semantic_planning``, ``fallback_semantic_planner_decision``,
``planner_history_messages``, ``SemanticPlanningResult``,
``ResolvePlanningDeps``, ``RunPlannerDeps``) has been removed. The live
agent-loop-first path no longer calls ``resolve_semantic_planning``.

This module now retains only the pure helpers that the live path still
consumes:

- :func:`annotation_quick_action_kind` — entry_action/task_mode → quick
  action kind.
- :func:`submission_mode` — entry_action/attachments → submission mode.
- :func:`quick_action_not_applicable` — builds a ``not_applicable`` quick
  action card dict.
- :func:`quick_action_label` — entry_action → Chinese label.
- :func:`quick_action_content` — assembles quick action Markdown content
  from a generated annotation.

The actual planning snapshot construction (working set derivation,
clarification rules, deictic detection) lives in ``planner.py``.
"""

from __future__ import annotations

from typing import Any, Literal

from app.schemas.reader_ask import (
    ReaderAskAttachment,
    ReaderAskEntryAction,
    ReaderAskSubmissionMode,
    ReaderAskTaskMode,
)


# ---------------------------------------------------------------------------
# Pure functions: quick action mapping
# ---------------------------------------------------------------------------

def annotation_quick_action_kind(
    task_mode: ReaderAskTaskMode,
    entry_action: ReaderAskEntryAction,
) -> Literal["grammar_note", "sentence_analysis"] | None:
    if task_mode == "grammar" or entry_action == "why_here":
        return "grammar_note"
    if task_mode == "breakdown" or entry_action == "explain_this":
        return "sentence_analysis"
    return None


def submission_mode(
    *,
    entry_action: ReaderAskEntryAction,
    attachments: list[ReaderAskAttachment],
) -> ReaderAskSubmissionMode:
    if entry_action not in {"why_here", "explain_this"}:
        return "chat"
    if any(attachment.metadata.source_surface == "selection_toolbar" for attachment in attachments):
        return "quick_action"
    return "chat"


def quick_action_not_applicable(
    *,
    kind: Literal["grammar_note", "sentence_analysis"],
    sentence_id: str,
    sentence_text: str,
    focus_text: str,
    reason: str,
    suggestion: str,
) -> dict[str, Any]:
    return {
        "status": "not_applicable",
        "kind": kind,
        "sentence_id": sentence_id,
        "source_sentence": sentence_text,
        "focus_text": focus_text,
        "reason": reason,
        "suggestion": suggestion,
    }


def quick_action_label(entry_action: ReaderAskEntryAction) -> str:
    if entry_action == "why_here":
        return "语法解析"
    if entry_action == "explain_this":
        return "句子拆分"
    return "快捷分析"


def quick_action_content(
    *,
    entry_action: ReaderAskEntryAction,
    generated_annotation: dict[str, Any] | None,
) -> str:
    if generated_annotation is None:
        return f"{quick_action_label(entry_action)}暂时无法完成。"
    if generated_annotation.get("status") == "not_applicable":
        reason = str(generated_annotation.get("reason") or "").strip()
        suggestion = str(generated_annotation.get("suggestion") or "").strip()
        pieces = [f"这次没有直接生成{quick_action_label(entry_action)}卡。"]
        if reason:
            pieces.append(reason)
        if suggestion:
            pieces.append(suggestion)
        return "\n\n".join(pieces)

    label = str(generated_annotation.get("label") or "").strip()
    focus_text = str(generated_annotation.get("focus_text") or "").strip()
    if generated_annotation.get("kind") == "grammar_note":
        note = str(generated_annotation.get("note_zh") or generated_annotation.get("content") or "").strip()
        scope_hint = (
            "我先基于整句理解，再聚焦你选中的片段。"
            if generated_annotation.get("analysis_scope") == "focus_span"
            else "我直接围绕这句话的关键结构来解释。"
        )
        pieces = [scope_hint]
        if label:
            pieces.append(f"关键语法点：**{label}**")
        if focus_text and generated_annotation.get("analysis_scope") == "focus_span":
            pieces.append(f"聚焦片段：`{focus_text}`")
        if note:
            pieces.append(note)
        return "\n\n".join(pieces)

    analysis = str(generated_annotation.get("analysis_zh") or generated_annotation.get("content") or "").strip()
    pieces = ["我先给你一个结构拆解卡，再补一句阅读顺序说明。"]
    if label:
        pieces.append(f"句型概述：**{label}**")
    if analysis:
        pieces.append(analysis)
    return "\n\n".join(pieces)
