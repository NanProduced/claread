"""Round 2: tool surface unit tests.

Covers the new ``ReaderAskAgentDeps`` Round 2 contract:
- ``get_record_context(scope, target_sentence_id)`` — window/paragraph/full
- ``get_record_insights(target_sentence_id, kind, limit)`` — narrow filters
  + workflow translation
- ``get_user_vocabulary_book(lemma, limit, sort_by)`` — replace
  ``search_user_vocabulary``
- ``resolve_known_reference(query, top_k)`` — resolved / ambiguous /
  not_found (no cross-HTTP HITL resume this round)
- ``suggest_prompts(suggestions)`` — 2-3 follow-up suggestions

These tests live alongside the existing ``test_reader_ask_tool_*`` modules
but are kept separate to keep the Round 2 surface under one
``test_reader_ask_tool_surface_round2.py`` umbrella.
"""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.reader_ask_agent import (
    ReaderAskAgentDeps,
    ReaderAskRuntimeState,
    _suggest_prompts_tool,
)
from app.agents.reader_ask_tool_policy import build_tool_availability
from app.agents.reader_ask_tool_registry import (
    READER_ASK_TOOL_REGISTRY,
    TOOL_GET_RECORD_CONTEXT,
    TOOL_GET_RECORD_INSIGHTS,
    TOOL_GET_USER_VOCABULARY_BOOK,
    TOOL_LOOKUP_DICTIONARY_ENTRY,
    TOOL_LOOKUP_RECORD_BY_EMBEDDING,
    TOOL_RESOLVE_KNOWN_REFERENCE,
    TOOL_RUN_DICTIONARY_AI_CONTEXT_EXPLAIN,
    TOOL_SUGGEST_PROMPTS,
    agent_callable_tool_names,
)
from app.schemas.reader_ask import ReaderAskAnchorRef


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _anchor() -> ReaderAskAnchorRef:
    return ReaderAskAnchorRef(
        anchor_type="sentence",
        target_key="record:r1:sentence:s1",
        sentence_id="s1",
        selected_text="test",
    )


def _make_record(
    *,
    title: str = "Test",
    sentences: list[dict] | None = None,
    source_text: str = "",
    sentence_entries: list[dict] | None = None,
    translations: list[dict] | None = None,
    paragraph_id_by_sentence: dict[str, str] | None = None,
) -> SimpleNamespace:
    render_scene: dict = {"article": {"sentences": sentences or []}}
    if sentence_entries is not None:
        render_scene["sentence_entries"] = sentence_entries
    if translations is not None:
        render_scene["translations"] = translations
    return SimpleNamespace(
        record_id="r1",
        title=title,
        source_text=source_text,
        render_scene=render_scene,
        page_state_json={},
        workflow_version=None,
        schema_version=None,
    )


def _make_deps(**overrides: object) -> ReaderAskAgentDeps:
    state = ReaderAskRuntimeState()
    kwargs: dict = dict(
        payload={},
        event_queue=AsyncMock(),
        state=state,
        query_seed="test",
        task_mode="general",
        record_id="r1",
        record_title="Test",
        primary_anchor=_anchor(),
        get_record_context_fn=AsyncMock(
            return_value={"record_id": "r1", "sentence_window": []}
        ),
        get_record_insights_fn=AsyncMock(return_value=[]),
        get_user_vocabulary_book_fn=AsyncMock(return_value=[]),
        resolve_known_reference_fn=AsyncMock(
            return_value={"status": "not_found", "ok": False}
        ),
        generate_sentence_annotation_fn=AsyncMock(return_value=None),
        suggest_prompts_fn=AsyncMock(
            return_value={"status": "success", "suggestions": []}
        ),
        vocabulary_item_to_citation_fn=MagicMock(),
        tool_availability=build_tool_availability(
            SimpleNamespace(
                task_mode="general",
                entry_action="ask_about_this",
                has_primary_anchor=True,
                has_dictionary_anchor=False,
                has_generated_annotation_cache=False,
            )
        ),
    )
    kwargs.update(overrides)
    return ReaderAskAgentDeps(**kwargs)


def _make_ctx(**overrides: object) -> MagicMock:
    deps = _make_deps(**overrides)
    ctx = MagicMock()
    ctx.deps = deps
    return ctx


# ---------------------------------------------------------------------------
# Section A: Round 2 tool surface registry — what the main agent sees
# ---------------------------------------------------------------------------


class TestRound2AgentSurface:
    def test_exactly_eight_agent_callable_tools(self) -> None:
        assert len(agent_callable_tool_names()) == 8

    def test_agent_callable_set_lists_round2_tools(self) -> None:
        names = agent_callable_tool_names()
        expected = {
            TOOL_GET_RECORD_CONTEXT,
            TOOL_GET_RECORD_INSIGHTS,
            TOOL_GET_USER_VOCABULARY_BOOK,
            TOOL_RESOLVE_KNOWN_REFERENCE,
            TOOL_SUGGEST_PROMPTS,
        }
        assert expected.issubset(names)
        # Reserved RAG + deprecated dictionary are NOT
        # agent-callable. Round 5: search_user_vocabulary fully removed.
        assert TOOL_LOOKUP_RECORD_BY_EMBEDDING not in names
        assert "search_user_vocabulary" not in names
        assert TOOL_LOOKUP_DICTIONARY_ENTRY not in names
        assert TOOL_RUN_DICTIONARY_AI_CONTEXT_EXPLAIN not in names

    def test_reserved_rag_tool_registered_with_agent_callable_false(self) -> None:
        spec = READER_ASK_TOOL_REGISTRY[TOOL_LOOKUP_RECORD_BY_EMBEDDING]
        assert spec.agent_callable is False


# ---------------------------------------------------------------------------
# Section B: get_record_context tool contract
# ---------------------------------------------------------------------------


def _service_build_record_context_payload(record, *, scope, target_sentence_id=None):
    """Import the service helper lazily and call it with the right kwargs."""
    from app.services.reader_ask.service import _build_record_context_payload
    return _build_record_context_payload(
        record, scope=scope, target_sentence_id=target_sentence_id
    )


class TestBuildRecordContextPayloadWindow:
    def test_window_returns_sentence_window_field(self) -> None:
        record = _make_record(
            sentences=[
                {"sentence_id": "s1", "paragraph_id": "p1", "text": "Sentence one."},
                {"sentence_id": "s2", "paragraph_id": "p1", "text": "Sentence two."},
                {"sentence_id": "s3", "paragraph_id": "p1", "text": "Sentence three."},
            ]
        )
        result = _service_build_record_context_payload(
            record, scope="window", target_sentence_id=None
        )

        assert result["record_id"] == "r1"
        assert result["scope"] == "window"
        assert result["can_load_more"] == "window"
        assert isinstance(result["sentence_window"], list)
        # First two sentences form the default fallback window.
        assert result["sentence_window"][0]["text"] == "Sentence one."


class TestBuildRecordContextPayloadParagraph:
    def test_paragraph_returns_paragraph_sentences(self) -> None:
        record = _make_record(
            sentences=[
                {"sentence_id": "s1", "paragraph_id": "p1", "text": "P1-A"},
                {"sentence_id": "s2", "paragraph_id": "p1", "text": "P1-B"},
                {"sentence_id": "s3", "paragraph_id": "p2", "text": "P2-A"},
            ]
        )
        result = _service_build_record_context_payload(
            record, scope="paragraph", target_sentence_id="s1"
        )

        # Only paragraph p1's sentences are returned.
        ids = [s["sentence_id"] for s in result["sentence_window"]]
        assert ids == ["s1", "s2"]
        # target sentence_id echoes back.
        assert result["target_sentence_id"] == "s1"

    def test_paragraph_falls_back_to_window_when_no_target(self) -> None:
        record = _make_record(
            sentences=[
                {"sentence_id": "s1", "paragraph_id": "p1", "text": "Only one."},
            ]
        )
        # No target_sentence_id + paragraph scope ⇒ falls back to default
        # window from _collect_sentence_windows.
        result = _service_build_record_context_payload(
            record, scope="paragraph", target_sentence_id=None
        )
        assert isinstance(result["sentence_window"], list)


class TestBuildRecordContextPayloadFull:
    def test_full_caps_at_10000_chars(self) -> None:
        long_text = "x" * 15000
        record = _make_record(source_text=long_text)
        result = _service_build_record_context_payload(
            record, scope="full", target_sentence_id=None
        )

        assert result["truncated"] is True
        # The first entry's text is the capped full article.
        assert len(result["sentence_window"]) == 1
        assert len(result["sentence_window"][0]["text"]) == 10000

    def test_full_under_limit_not_truncated(self) -> None:
        record = _make_record(source_text="short article body")
        result = _service_build_record_context_payload(
            record, scope="full", target_sentence_id=None
        )

        assert result["truncated"] is False
        assert result["sentence_window"][0]["text"] == "short article body"


class TestFormatSentenceSpan:
    def test_truncates_text_to_320_chars(self) -> None:
        from app.services.reader_ask.service import _format_sentence_span
        sentence = {"sentence_id": "s1", "paragraph_id": "p1", "text": "y" * 500}
        result = _format_sentence_span(sentence)
        assert result["sentence_id"] == "s1"
        assert result["paragraph_id"] == "p1"
        # truncate_text appends "..."; assert length is at most limit+3.
        assert len(result["text"]) <= 323
        assert result["text"].endswith("...")
        assert result["is_active_anchor"] is False


class TestCollectParagraphSentences:
    def test_returns_only_paragraph_matches(self) -> None:
        from app.services.reader_ask.service import _collect_paragraph_sentences
        record = _make_record(
            sentences=[
                {"sentence_id": "s1", "paragraph_id": "p1", "text": "A"},
                {"sentence_id": "s2", "paragraph_id": "p1", "text": "B"},
                {"sentence_id": "s3", "paragraph_id": "p2", "text": "C"},
            ]
        )
        result = _collect_paragraph_sentences(record, target_sentence_id="s2")
        ids = [s["sentence_id"] for s in result]
        assert ids == ["s1", "s2"]


# ---------------------------------------------------------------------------
# Section C: get_record_insights tool contract
# ---------------------------------------------------------------------------


def _service_collect_insight_entries():
    from app.services.reader_ask.service import _collect_insight_entries
    return _collect_insight_entries


class TestCollectInsightEntries:
    def test_no_filter_returns_all(self) -> None:
        record = _make_record(
            sentence_entries=[
                {
                    "id": "e1",
                    "sentence_id": "s1",
                    "entry_type": "grammar_note",
                    "title": "T1",
                    "content": "C1",
                },
                {
                    "id": "e2",
                    "sentence_id": "s2",
                    "entry_type": "vocabulary",
                    "title": "T2",
                    "content": "C2",
                },
            ]
        )
        collect = _service_collect_insight_entries()
        result = collect(
            record, target_sentence_id=None, kind=None, limit=5
        )
        assert len(result) == 2

    def test_kind_filter(self) -> None:
        record = _make_record(
            sentence_entries=[
                {
                    "id": "e1",
                    "sentence_id": "s1",
                    "entry_type": "grammar_note",
                    "title": "T1",
                    "content": "C1",
                },
                {
                    "id": "e2",
                    "sentence_id": "s2",
                    "entry_type": "vocabulary",
                    "title": "T2",
                    "content": "C2",
                },
            ]
        )
        collect = _service_collect_insight_entries()
        result = collect(
            record, target_sentence_id=None, kind="grammar_note", limit=5
        )
        assert len(result) == 1
        assert result[0]["kind"] == "grammar_note"
        assert result[0]["insight_id"] == "e1"

    def test_target_sentence_id_filter(self) -> None:
        record = _make_record(
            sentence_entries=[
                {
                    "id": "e1",
                    "sentence_id": "s1",
                    "entry_type": "grammar_note",
                    "title": "T1",
                    "content": "C1",
                },
                {
                    "id": "e2",
                    "sentence_id": "s2",
                    "entry_type": "vocabulary",
                    "title": "T2",
                    "content": "C2",
                },
            ]
        )
        collect = _service_collect_insight_entries()
        result = collect(
            record, target_sentence_id="s2", kind=None, limit=5
        )
        assert len(result) == 1
        assert result[0]["sentence_id"] == "s2"

    def test_translation_zh_carried_from_render_scene(self) -> None:
        record = _make_record(
            sentence_entries=[
                {
                    "id": "e1",
                    "sentence_id": "s1",
                    "entry_type": "grammar_note",
                    "title": "T1",
                    "content": "C1",
                },
            ],
            translations=[
                {"sentence_id": "s1", "translation_zh": "句子的中文翻译。"}
            ],
        )
        collect = _service_collect_insight_entries()
        result = collect(
            record, target_sentence_id=None, kind=None, limit=5
        )
        assert result[0]["translation_zh"] == "句子的中文翻译。"
        assert result[0]["source"] == "workflow"

    def test_limit_respected(self) -> None:
        record = _make_record(
            sentence_entries=[
                {
                    "id": f"e{i}",
                    "sentence_id": f"s{i}",
                    "entry_type": "grammar_note",
                    "title": f"T{i}",
                    "content": "C",
                }
                for i in range(10)
            ]
        )
        collect = _service_collect_insight_entries()
        result = collect(
            record, target_sentence_id=None, kind=None, limit=3
        )
        assert len(result) == 3


# ---------------------------------------------------------------------------
# Section D: get_user_vocabulary_book tool (mocks vocabulary_svc)
# ---------------------------------------------------------------------------


def _service_tool_get_user_vocabulary_book():
    from app.services.reader_ask.service import _tool_get_user_vocabulary_book
    return _tool_get_user_vocabulary_book


def _patched_vocab(monkeypatch: pytest.MonkeyPatch, items: list[dict]) -> None:
    """Patch vocabulary_svc.list_vocabulary to return a fixed item list."""
    from app.services.reader_ask import service

    async def fake_list_vocabulary(*, user_id, page, limit, lite):  # noqa: ARG001
        return items, len(items)

    monkeypatch.setattr(
        service.vocabulary_svc,
        "list_vocabulary",
        fake_list_vocabulary,
    )


class TestGetUserVocabularyBook:
    def test_lemma_filter_substring(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patched_vocab(
            monkeypatch,
            [
                {
                    "id": "1",
                    "lemma": "chronic",
                    "display_word": "chronic absenteeism",
                    "short_meaning": "长期旷课",
                    "source_sentence": "students miss school often",
                    "mastery_status": "new",
                    "created_at": "2026-01-01T00:00:00Z",
                },
                {
                    "id": "2",
                    "lemma": "resilient",
                    "display_word": "resilient",
                    "short_meaning": "有韧性的",
                    "source_sentence": "stay strong",
                    "mastery_status": "new",
                    "created_at": "2026-02-01T00:00:00Z",
                },
            ],
        )
        tool = _service_tool_get_user_vocabulary_book()
        results = asyncio.run(
            tool(
                "00000000-0000-0000-0000-000000000000",
                lemma="chronic",
                limit=10,
                sort_by="recent",
            )
        )
        assert len(results) == 1
        assert results[0]["lemma"] == "chronic"

    def test_sort_by_recent_uses_created_at_desc(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patched_vocab(
            monkeypatch,
            [
                {
                    "id": "1",
                    "lemma": "alpha",
                    "display_word": "alpha",
                    "short_meaning": "α",
                    "source_sentence": "",
                    "mastery_status": "new",
                    "created_at": "2026-01-01T00:00:00Z",
                },
                {
                    "id": "2",
                    "lemma": "beta",
                    "display_word": "beta",
                    "short_meaning": "β",
                    "source_sentence": "",
                    "mastery_status": "new",
                    "created_at": "2026-05-01T00:00:00Z",
                },
            ],
        )
        tool = _service_tool_get_user_vocabulary_book()
        results = asyncio.run(
            tool(
                "00000000-0000-0000-0000-000000000000",
                lemma=None,
                limit=10,
                sort_by="recent",
            )
        )
        # 'recent' order: beta (newer) before alpha.
        assert [r["lemma"] for r in results] == ["beta", "alpha"]

    def test_sort_by_lemma_asc(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patched_vocab(
            monkeypatch,
            [
                {
                    "id": "1",
                    "lemma": "zebra",
                    "display_word": "zebra",
                    "short_meaning": "",
                    "source_sentence": "",
                    "mastery_status": "new",
                    "created_at": "2026-01-01T00:00:00Z",
                },
                {
                    "id": "2",
                    "lemma": "apple",
                    "display_word": "apple",
                    "short_meaning": "",
                    "source_sentence": "",
                    "mastery_status": "new",
                    "created_at": "2026-05-01T00:00:00Z",
                },
            ],
        )
        tool = _service_tool_get_user_vocabulary_book()
        results = asyncio.run(
            tool(
                "00000000-0000-0000-0000-000000000000",
                lemma=None,
                limit=10,
                sort_by="lemma_asc",
            )
        )
        assert [r["lemma"] for r in results] == ["apple", "zebra"]

    def test_limit_caps_results(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patched_vocab(
            monkeypatch,
            [
                {
                    "id": str(i),
                    "lemma": f"word{i}",
                    "display_word": f"word{i}",
                    "short_meaning": "",
                    "source_sentence": "",
                    "mastery_status": "new",
                    "created_at": f"2026-{i:02d}-01T00:00:00Z",
                }
                for i in range(1, 11)
            ],
        )
        tool = _service_tool_get_user_vocabulary_book()
        results = asyncio.run(
            tool(
                "00000000-0000-0000-0000-000000000000",
                lemma=None,
                limit=3,
                sort_by="recent",
            )
        )
        assert len(results) == 3

    def test_empty_input_returns_empty_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patched_vocab(monkeypatch, [])
        tool = _service_tool_get_user_vocabulary_book()
        results = asyncio.run(
            tool(
                "00000000-0000-0000-0000-000000000000",
                lemma="anything",
                limit=10,
                sort_by="recent",
            )
        )
        assert results == []


# ---------------------------------------------------------------------------
# Section E: resolve_known_reference tool — three-state contract
# ---------------------------------------------------------------------------


def _service_resolve_known_reference():
    from app.services.reader_ask.service import _tool_resolve_known_reference_for_agent
    return _tool_resolve_known_reference_for_agent


def _patch_resolver(monkeypatch: pytest.MonkeyPatch, resolution) -> None:
    """Patch resolver.resolve_known_references to return a fixed resolution."""
    from app.services.reader_ask import resolver

    async def fake_resolve_known_references(*, user_id, current_record_id, reference_needs):  # noqa: ARG001
        return resolution

    monkeypatch.setattr(
        resolver,
        "resolve_known_references",
        fake_resolve_known_references,
    )


def _make_resolution(status: str, *, query: str = "q") -> object:
    return SimpleNamespace(
        attempted=True,
        status=status,
        query=query,
        reason="test",
        resolved_records=[{"record_id": "r1", "title": "Article one"}] if status == "resolved" else [],
        ambiguous_records=[
            {"record_id": "r1", "title": "A"},
            {"record_id": "r2", "title": "B"},
        ] if status == "ambiguous" else [],
        resolution_meta={},
    )


class TestResolveKnownReference:
    def test_resolved_state(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_resolver(monkeypatch, _make_resolution("resolved"))
        tool = _service_resolve_known_reference()
        result = asyncio.run(
            tool(
                user_id="00000000-0000-0000-0000-000000000000",
                current_record_id="00000000-0000-0000-0000-000000000001",
                query="chronic absenteeism",
                top_k=5,
            )
        )
        assert result["status"] == "resolved"
        assert result["ok"] is True
        assert result["disambiguation_needed"] is False
        assert result["record"]["record_id"] == "r1"
        assert any("record:r1" in a for a in result["artifacts"])

    def test_ambiguous_state_marks_disambiguation_needed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_resolver(monkeypatch, _make_resolution("ambiguous"))
        tool = _service_resolve_known_reference()
        result = asyncio.run(
            tool(
                user_id="00000000-0000-0000-0000-000000000000",
                current_record_id="00000000-0000-0000-0000-000000000001",
                query="chronic",
                top_k=5,
            )
        )
        assert result["status"] == "ambiguous"
        assert result["ok"] is True
        assert result["disambiguation_needed"] is True
        assert len(result["candidates"]) == 2
        # No resolved record set when ambiguous.
        assert "record" not in result or result.get("record") is None

    def test_not_found_state(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_resolver(monkeypatch, _make_resolution("not_found"))
        tool = _service_resolve_known_reference()
        result = asyncio.run(
            tool(
                user_id="00000000-0000-0000-0000-000000000000",
                current_record_id="00000000-0000-0000-0000-000000000001",
                query="nonexistent",
                top_k=5,
            )
        )
        assert result["status"] == "not_found"
        assert result["ok"] is False
        assert result["disambiguation_needed"] is False
        # not_found returns no candidates (the field is absent or empty).
        assert result.get("candidates", []) == []

    def test_top_k_limits_candidates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_resolver(monkeypatch, _make_resolution("ambiguous"))
        tool = _service_resolve_known_reference()
        result = asyncio.run(
            tool(
                user_id="00000000-0000-0000-0000-000000000000",
                current_record_id="00000000-0000-0000-0000-000000000001",
                query="q",
                top_k=1,
            )
        )
        # Despite two ambiguous candidates, top_k=1 caps them.
        assert len(result["candidates"]) == 1


# ---------------------------------------------------------------------------
# Section F: suggest_prompts tool contract
# ---------------------------------------------------------------------------


class TestSuggestPromptsTool:
    def _ctx_with_echo_fn(self) -> MagicMock:
        """Build a ctx whose suggest_prompts_fn echoes back the suggestions."""
        async def echo(suggestions: list[dict[str, Any]]) -> dict[str, Any]:
            return {
                "status": "success",
                "summary": f"Suggested {len(suggestions)} follow-up prompt(s).",
                "ok": True,
                "suggestions": suggestions,
            }
        return _make_ctx(suggest_prompts_fn=echo)

    def test_returns_warning_when_no_suggestions(self) -> None:
        ctx = self._ctx_with_echo_fn()
        result = asyncio.run(_suggest_prompts_tool(ctx, suggestions=None))
        assert result["status"] == "warning"
        assert result["ok"] is False

    def test_returns_warning_when_only_one_valid(self) -> None:
        ctx = self._ctx_with_echo_fn()
        result = asyncio.run(
            _suggest_prompts_tool(
                ctx,
                suggestions=[{"label": "Only one", "prompt": "go"}],
            )
        )
        assert result["status"] == "warning"
        # 2-3 required, only 1 provided.
        assert result["ok"] is False

    def test_clamps_to_three_max(self) -> None:
        ctx = self._ctx_with_echo_fn()
        suggestions = [
            {"label": f"S{i}", "prompt": f"prompt-{i}"} for i in range(5)
        ]
        result = asyncio.run(
            _suggest_prompts_tool(ctx, suggestions=suggestions)
        )
        assert result["status"] == "success"
        assert len(result["suggestions"]) == 3
        # Stored on state for the completed payload to consume.
        assert len(ctx.deps.state.latest_suggestions) == 3

    def test_truncates_label_and_prompt_to_safe_lengths(self) -> None:
        ctx = self._ctx_with_echo_fn()
        result = asyncio.run(
            _suggest_prompts_tool(
                ctx,
                suggestions=[
                    {
                        "label": "L" * 200,
                        "prompt": "P" * 500,
                    },
                    {"label": "short", "prompt": "short"},
                ],
            )
        )
        assert result["status"] == "success"
        first = result["suggestions"][0]
        assert len(first["label"]) == 40
        assert len(first["prompt"]) == 200

    def test_ignores_invalid_items(self) -> None:
        ctx = self._ctx_with_echo_fn()
        # Mix valid + invalid items. The tool clamps to first 3, so put
        # the two valid items first to make sure they survive.
        result = asyncio.run(
            _suggest_prompts_tool(
                ctx,
                suggestions=[
                    {"label": "valid-1", "prompt": "p-1"},
                    {"label": "valid-2", "prompt": "p-2"},
                    {"label": "", "prompt": "no label"},
                    {"label": "no prompt"},
                    "not a dict",
                ],
            )
        )
        assert result["status"] == "success"
        # Only 2 valid items survived.
        assert len(result["suggestions"]) == 2


# ---------------------------------------------------------------------------
# Section G: agent tool registration — model-facing surface
# ---------------------------------------------------------------------------


class TestAgentModuleToolRegistration:
    def test_agent_registers_only_round2_agent_callable_tools(self) -> None:
        """Static check: the agent module's @agent.tool decorators must only
        use Round 2 agent-callable constants (no deprecated / reserved)."""
        import ast
        from pathlib import Path

        agent_path = (
            Path(__file__).resolve().parent.parent
            / "app"
            / "agents"
            / "reader_ask_agent.py"
        )
        tree = ast.parse(agent_path.read_text(encoding="utf-8"))
        decorator_names: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for deco in node.decorator_list:
                if not isinstance(deco, ast.Call):
                    continue
                if not isinstance(deco.func, ast.Attribute):
                    continue
                if deco.func.attr != "tool":
                    continue
                for kw in deco.keywords:
                    if kw.arg == "name" and isinstance(kw.value, ast.Name):
                        decorator_names.append(kw.value.id)

        # All decorator names must reference agent-callable tools.
        allowed = agent_callable_tool_names()
        # Decode constants to actual string values for cross-check.
        from app.agents import reader_ask_tool_registry as reg

        allowed_constants = {
            getattr(reg, name)
            for name in (
                "TOOL_GET_RECORD_CONTEXT",
                "TOOL_GET_RECORD_INSIGHTS",
                "TOOL_GET_USER_VOCABULARY_BOOK",
                "TOOL_RESOLVE_KNOWN_REFERENCE",
                "TOOL_GENERATE_SENTENCE_ANNOTATION",
                "TOOL_PROPOSE_SAVE_NOTE",
                "TOOL_PROPOSE_SAVE_HIGHLIGHT",
                "TOOL_SUGGEST_PROMPTS",
            )
        }
        # Each decorator name corresponds to a TOOL_* constant whose value
        # is in the agent-callable set.
        for name in decorator_names:
            value = getattr(reg, name, None)
            assert isinstance(value, str), f"{name} is not a TOOL_* constant"
            assert value in allowed_constants, (
                f"{name} resolves to {value!r} which is not agent-callable"
            )
            assert value in allowed
