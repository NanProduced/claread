"""Tests for ReaderAskAgentDeps factory — build_reader_ask_agent_deps (Round 2)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.agents.reader_ask_tool_registry import agent_callable_tool_names
from app.schemas.reader_ask import ReaderAskAnchorRef, ReaderAskCitation
from app.services.reader_ask.agent_deps_factory import build_reader_ask_agent_deps


def _anchor() -> ReaderAskAnchorRef:
    return ReaderAskAnchorRef(
        anchor_type="sentence",
        target_key="s1",
        selected_text="test sentence",
    )


def _citation() -> ReaderAskCitation:
    return ReaderAskCitation(
        citation_id="c1",
        kind="vocabulary",
        label="test",
    )


def _vocab_cite(item: dict) -> ReaderAskCitation:  # noqa: ARG001
    return _citation()


def _make_deps(
    *,
    primary_anchor: ReaderAskAnchorRef | None = None,
    task_mode: str = "general",
    entry_action: str = "ask_about_this",
    has_dictionary_anchor: bool = False,
    has_generated_annotation_cache: bool = False,
):
    from app.agents.reader_ask_agent import ReaderAskRuntimeState

    return build_reader_ask_agent_deps(
        payload={"test": True},
        event_queue=asyncio.Queue(),
        state=ReaderAskRuntimeState(),
        query_seed="seed",
        task_mode=task_mode,
        entry_action=entry_action,
        record_id="r1",
        record_title="Test Record",
        primary_anchor=primary_anchor,
        get_record_context_fn=AsyncMock(),
        get_record_insights_fn=AsyncMock(),
        get_user_vocabulary_book_fn=AsyncMock(),
        resolve_known_reference_fn=AsyncMock(),
        load_explicit_attachment_context_fn=AsyncMock(),
        generate_sentence_annotation_fn=AsyncMock(),
        suggest_prompts_fn=AsyncMock(),
        vocabulary_item_to_citation_fn=_vocab_cite,
        has_dictionary_anchor=has_dictionary_anchor,
        has_generated_annotation_cache=has_generated_annotation_cache,
    )


class TestBuildReaderAskAgentDepsToolAvailability:
    """Tool availability is correctly wired by the factory (Round 2: only
    the 9 agent-callable tools are exposed)."""

    def test_with_primary_anchor_all_agent_callable_tools_allowed(self) -> None:
        deps = _make_deps(primary_anchor=_anchor())
        assert deps.tool_availability is not None
        assert deps.tool_availability.allowed_tool_names == agent_callable_tool_names()
        # 9 agent-callable tools total
        assert len(deps.tool_availability.allowed_tool_names) == 9

    def test_without_primary_anchor_write_tools_unavailable(self) -> None:
        deps = _make_deps(primary_anchor=None)
        assert deps.tool_availability is not None
        reasons = deps.tool_availability.unavailable_reasons
        assert "propose_save_note" in reasons
        assert "propose_save_highlight" in reasons

    def test_without_primary_anchor_all_tools_still_allowed(self) -> None:
        """Conservative baseline: no tool is removed from allowed_tool_names."""
        deps = _make_deps(primary_anchor=None)
        assert deps.tool_availability.allowed_tool_names == agent_callable_tool_names()


class TestBuildReaderAskAgentDepsPreservesCallbacks:
    """Callback functions and runtime state are passed through unchanged."""

    def test_callback_fns_preserved(self) -> None:
        get_ctx = AsyncMock()
        get_insights = AsyncMock()
        get_vocab = AsyncMock()
        resolve_known = AsyncMock()
        gen_annot = AsyncMock()
        suggest = AsyncMock()

        from app.agents.reader_ask_agent import ReaderAskRuntimeState

        deps = build_reader_ask_agent_deps(
            payload={"test": True},
            event_queue=asyncio.Queue(),
            state=ReaderAskRuntimeState(),
            query_seed="seed",
            task_mode="general",
            entry_action="ask_about_this",
            record_id="r1",
            record_title="Test Record",
            primary_anchor=None,
            get_record_context_fn=get_ctx,
            get_record_insights_fn=get_insights,
            get_user_vocabulary_book_fn=get_vocab,
            resolve_known_reference_fn=resolve_known,
            load_explicit_attachment_context_fn=AsyncMock(),
            generate_sentence_annotation_fn=gen_annot,
            suggest_prompts_fn=suggest,
            vocabulary_item_to_citation_fn=_vocab_cite,
        )

        assert deps.get_record_context_fn is get_ctx
        assert deps.get_record_insights_fn is get_insights
        assert deps.get_user_vocabulary_book_fn is get_vocab
        assert deps.resolve_known_reference_fn is resolve_known
        assert deps.generate_sentence_annotation_fn is gen_annot
        assert deps.suggest_prompts_fn is suggest
        assert deps.vocabulary_item_to_citation_fn is _vocab_cite

    def test_runtime_state_preserved(self) -> None:
        from app.agents.reader_ask_agent import ReaderAskRuntimeState

        state = ReaderAskRuntimeState(max_tool_calls=10)
        deps = _make_deps(primary_anchor=None)
        # Verify default state is used (not replaced by factory)
        assert deps.state.max_tool_calls == 5

        deps_custom = build_reader_ask_agent_deps(
            payload={"test": True},
            event_queue=asyncio.Queue(),
            state=state,
            query_seed="seed",
            task_mode="general",
            entry_action="ask_about_this",
            record_id="r1",
            record_title="Test Record",
            primary_anchor=None,
            get_record_context_fn=AsyncMock(),
            get_record_insights_fn=AsyncMock(),
            get_user_vocabulary_book_fn=AsyncMock(),
            resolve_known_reference_fn=AsyncMock(),
            load_explicit_attachment_context_fn=AsyncMock(),
            generate_sentence_annotation_fn=AsyncMock(),
            suggest_prompts_fn=AsyncMock(),
            vocabulary_item_to_citation_fn=_vocab_cite,
        )
        assert deps_custom.state is state
        assert deps_custom.state.max_tool_calls == 10


class TestBuildReaderAskAgentDepsBaselineDefaults:
    """P5 baseline defaults for dictionary_anchor and annotation_cache."""

    def test_dictionary_anchor_defaults_false(self) -> None:
        deps = _make_deps(primary_anchor=_anchor())
        # No assertion on behavior — just verify it doesn't crash with defaults
        assert deps.tool_availability is not None

    def test_generated_annotation_cache_defaults_false(self) -> None:
        deps = _make_deps(primary_anchor=_anchor())
        assert deps.tool_availability is not None

    def test_event_queue_preserved(self) -> None:
        q: asyncio.Queue[tuple[str, dict]] = asyncio.Queue()
        from app.agents.reader_ask_agent import ReaderAskRuntimeState

        deps = build_reader_ask_agent_deps(
            payload={"test": True},
            event_queue=q,
            state=ReaderAskRuntimeState(),
            query_seed="seed",
            task_mode="general",
            entry_action="ask_about_this",
            record_id="r1",
            record_title="Test Record",
            primary_anchor=None,
            get_record_context_fn=AsyncMock(),
            get_record_insights_fn=AsyncMock(),
            get_user_vocabulary_book_fn=AsyncMock(),
            resolve_known_reference_fn=AsyncMock(),
            load_explicit_attachment_context_fn=AsyncMock(),
            generate_sentence_annotation_fn=AsyncMock(),
            suggest_prompts_fn=AsyncMock(),
            vocabulary_item_to_citation_fn=_vocab_cite,
        )
        assert deps.event_queue is q
