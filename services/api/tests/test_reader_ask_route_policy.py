"""Round 3 route policy tests for the Ask Claread planner route resolution.

These tests cover the route policy introduced in Round 3, where the default
flipped from planner-first to agent-loop-first:

- ``fast_path_runtime.resolve_planner_route`` decision logic.
- ``fast_path_runtime.PlannerRoute`` type contract.

See ``fast_path_runtime`` module docstring for the design rationale.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.schemas.reader_ask import (
    ReaderAskAnchorRef,
    ReaderAskAttachment,
    ReaderAskAttachmentMetadata,
)
from app.services.reader_ask import fast_path_runtime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _anchor(anchor_type: str) -> ReaderAskAnchorRef:
    return ReaderAskAnchorRef(anchor_type=anchor_type, label="a", sentence_id="s1")  # type: ignore[arg-type]


def _dict_anchor() -> ReaderAskAnchorRef:
    return ReaderAskAnchorRef(anchor_type="dictionary_entry", label="dict", dict_entry_id=1)


def _attachment(kind: str, subtype: str = "x") -> ReaderAskAttachment:
    return ReaderAskAttachment(
        kind=kind,  # type: ignore[arg-type]
        subtype=subtype,
        label="att",
        metadata=ReaderAskAttachmentMetadata(source_surface="reader_page"),
    )


def _history(n: int) -> list[dict[str, Any]]:
    return [{"role": "user", "content_md": f"msg {i}"} for i in range(n)]


# ---------------------------------------------------------------------------
# resolve_planner_route
# ---------------------------------------------------------------------------


class TestResolvePlannerRoute:
    """Tests for :func:`resolve_planner_route`."""

    def test_default_returns_agent_loop_first(self) -> None:
        """Simple article-bound query with any entry_action, short history,
        no attachments defaults to agent_loop_first."""
        assert (
            fast_path_runtime.resolve_planner_route(
                entry_action="ask_about_this",
                history_messages=_history(2),
                attachments=[],
                anchors=[],
                cross_record_toggle=False,
                latest_user_message="这篇文章的主题是什么",
            )
            == "agent_loop_first"
        )

    def test_general_entry_action_returns_agent_loop_first(self) -> None:
        """The 'general' entry_action defaults to agent_loop_first.
        This is the key Round 3 change — entry_action is no longer a gate."""
        assert (
            fast_path_runtime.resolve_planner_route(
                entry_action="general",  # type: ignore[arg-type]
                history_messages=_history(2),
                attachments=[],
                anchors=[],
                cross_record_toggle=False,
                latest_user_message="这篇文章的主题是什么",
            )
            == "agent_loop_first"
        )

    def test_explain_this_backward_compat(self) -> None:
        assert (
            fast_path_runtime.resolve_planner_route(
                entry_action="explain_this",
                history_messages=_history(0),
                attachments=[],
                anchors=[_anchor("sentence")],
                cross_record_toggle=False,
                latest_user_message="解释一下",
            )
            == "agent_loop_first"
        )

    def test_ask_about_this_backward_compat(self) -> None:
        assert (
            fast_path_runtime.resolve_planner_route(
                entry_action="ask_about_this",
                history_messages=_history(0),
                attachments=[],
                anchors=[_anchor("sentence")],
                cross_record_toggle=False,
                latest_user_message="这句话想表达什么",
            )
            == "agent_loop_first"
        )

    def test_why_here_backward_compat(self) -> None:
        assert (
            fast_path_runtime.resolve_planner_route(
                entry_action="why_here",
                history_messages=_history(0),
                attachments=[],
                anchors=[_anchor("sentence")],
                cross_record_toggle=False,
                latest_user_message="这里为什么用 present perfect",
            )
            == "agent_loop_first"
        )

    def test_record_ref_attachment_returns_planner_first(self) -> None:
        assert (
            fast_path_runtime.resolve_planner_route(
                entry_action="ask_about_this",
                history_messages=_history(0),
                attachments=[_attachment("record_ref", "related_record")],
                anchors=[],
                cross_record_toggle=False,
                latest_user_message="对照我之前那篇",
            )
            == "planner_first"
        )

    def test_analysis_ref_attachment_returns_planner_first(self) -> None:
        assert (
            fast_path_runtime.resolve_planner_route(
                entry_action="ask_about_this",
                history_messages=_history(0),
                attachments=[_attachment("analysis_ref", "summary")],
                anchors=[],
                cross_record_toggle=False,
                latest_user_message="解释一下",
            )
            == "planner_first"
        )

    def test_supplement_ref_attachment_returns_planner_first(self) -> None:
        assert (
            fast_path_runtime.resolve_planner_route(
                entry_action="ask_about_this",
                history_messages=_history(0),
                attachments=[_attachment("supplement_ref", "grammar_note")],
                anchors=[],
                cross_record_toggle=False,
                latest_user_message="解释一下",
            )
            == "planner_first"
        )

    def test_dictionary_anchor_returns_planner_first(self) -> None:
        assert (
            fast_path_runtime.resolve_planner_route(
                entry_action="ask_about_this",
                history_messages=_history(0),
                attachments=[],
                anchors=[_dict_anchor()],
                cross_record_toggle=False,
                latest_user_message="这个词什么意思",
            )
            == "planner_first"
        )

    def test_dictionary_attachment_returns_planner_first(self) -> None:
        assert (
            fast_path_runtime.resolve_planner_route(
                entry_action="ask_about_this",
                history_messages=_history(0),
                attachments=[_attachment("text_selection", "dictionary_entry")],
                anchors=[],
                cross_record_toggle=False,
                latest_user_message="这个词什么意思",
            )
            == "planner_first"
        )

    def test_deictic_without_anchor_returns_planner_first(self) -> None:
        assert (
            fast_path_runtime.resolve_planner_route(
                entry_action="ask_about_this",
                history_messages=_history(0),
                attachments=[],
                anchors=[],
                cross_record_toggle=False,
                latest_user_message="解释这句",
            )
            == "planner_first"
        )

    def test_deictic_with_anchor_returns_agent_loop_first(self) -> None:
        """Anchor grounds the deictic reference, so agent-loop can handle it."""
        assert (
            fast_path_runtime.resolve_planner_route(
                entry_action="ask_about_this",
                history_messages=_history(0),
                attachments=[],
                anchors=[_anchor("sentence")],
                cross_record_toggle=False,
                latest_user_message="解释这句",
            )
            == "agent_loop_first"
        )

    def test_cross_record_toggle_with_keywords_returns_planner_first(self) -> None:
        assert (
            fast_path_runtime.resolve_planner_route(
                entry_action="ask_about_this",
                history_messages=_history(0),
                attachments=[],
                anchors=[],
                cross_record_toggle=True,
                latest_user_message="和我之前那篇文章有什么不同？",
            )
            == "planner_first"
        )

    def test_cross_record_toggle_without_keywords_returns_agent_loop_first(self) -> None:
        assert (
            fast_path_runtime.resolve_planner_route(
                entry_action="ask_about_this",
                history_messages=_history(0),
                attachments=[],
                anchors=[],
                cross_record_toggle=True,
                latest_user_message="这篇文章的主题是什么",
            )
            == "agent_loop_first"
        )

    def test_cross_record_off_with_keywords_returns_agent_loop_first(self) -> None:
        """Toggle off: agent handles via resolve_known_reference tool."""
        assert (
            fast_path_runtime.resolve_planner_route(
                entry_action="ask_about_this",
                history_messages=_history(0),
                attachments=[],
                anchors=[],
                cross_record_toggle=False,
                latest_user_message="和我之前那篇文章有什么不同？",
            )
            == "agent_loop_first"
        )

    def test_long_history_returns_planner_first(self) -> None:
        assert (
            fast_path_runtime.resolve_planner_route(
                entry_action="ask_about_this",
                history_messages=_history(11),
                attachments=[],
                anchors=[],
                cross_record_toggle=False,
                latest_user_message="继续",
            )
            == "planner_first"
        )

    def test_short_history_returns_agent_loop_first(self) -> None:
        assert (
            fast_path_runtime.resolve_planner_route(
                entry_action="ask_about_this",
                history_messages=_history(10),
                attachments=[],
                anchors=[],
                cross_record_toggle=False,
                latest_user_message="继续",
            )
            == "agent_loop_first"
        )


# ---------------------------------------------------------------------------
# PlannerRoute type
# ---------------------------------------------------------------------------


class TestPlannerRouteType:
    """Verify the PlannerRoute type contract."""

    def test_planner_route_is_literal_with_two_values(self) -> None:
        """PlannerRoute is a Literal type with exactly the two expected values."""
        args = fast_path_runtime.PlannerRoute.__args__
        assert set(args) == {"agent_loop_first", "planner_first"}

    def test_resolve_planner_route_returns_valid_value(self) -> None:
        """resolve_planner_route always returns a valid PlannerRoute value."""
        result = fast_path_runtime.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=[],
            attachments=[],
            anchors=[],
            cross_record_toggle=False,
            latest_user_message="hello",
        )
        assert result in fast_path_runtime.PlannerRoute.__args__
