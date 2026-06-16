"""Round 10 regression tests: external attachment migration.

These tests verify:
1. External attachments (record_ref / analysis_ref / supplement_ref) route to agent_loop_first
2. has_explicit_external_attachments() is a public API
3. build_agent_loop_context sets external_attachment_hint
4. external_attachment_hint flows to prompt payload
5. load_explicit_attachment_context tool is agent-callable
6. planner_first fallbacks preserved (dictionary, long history)
7. Tool registry invariants hold with new tool
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.agents.reader_ask_agent import (
    ReaderAskAgentDeps,
    ReaderAskRuntimeState,
    _load_explicit_attachment_context_tool,
)
from app.schemas.reader_ask import (
    ReaderAskAnchorRef,
    ReaderAskAttachment,
    ReaderAskAttachmentMetadata,
)
from app.services.reader_ask import planner_route_policy


def _make_loader_ctx(
    *,
    allowed_external_attachments: list[dict[str, str]],
    loader_result: dict | None = None,
) -> tuple[MagicMock, AsyncMock, ReaderAskRuntimeState]:
    state = ReaderAskRuntimeState()
    loader = AsyncMock(
        return_value=loader_result
        or {
            "status": "loaded",
            "record_id": "00000000-0000-0000-0000-000000000002",
            "record_title": "External",
            "article_overview": "Overview",
            "record_insights": [],
            "source_labels": ["external_attachment"],
            "ok": True,
        }
    )
    deps = ReaderAskAgentDeps(
        payload={},
        event_queue=AsyncMock(),
        state=state,
        query_seed="test",
        task_mode="general",
        record_id="00000000-0000-0000-0000-000000000001",
        record_title="Current",
        primary_anchor=None,
        get_record_context_fn=AsyncMock(return_value={}),
        get_record_insights_fn=AsyncMock(return_value=[]),
        get_user_vocabulary_book_fn=AsyncMock(return_value=[]),
        resolve_known_reference_fn=AsyncMock(return_value={"status": "not_found", "ok": False}),
        load_explicit_attachment_context_fn=loader,
        generate_sentence_annotation_fn=AsyncMock(return_value=None),
        suggest_prompts_fn=AsyncMock(return_value={"status": "success", "suggestions": []}),
        vocabulary_item_to_citation_fn=MagicMock(),
        allowed_external_attachments=allowed_external_attachments,
    )
    ctx = MagicMock()
    ctx.deps = deps
    return ctx, loader, state


# ---------------------------------------------------------------------------
# 1. External attachments route to agent_loop_first
# ---------------------------------------------------------------------------


class TestExternalAttachmentRoutesToAgentLoopFirst:
    """Verify that external attachments no longer trigger planner_first."""

    def test_record_ref_returns_agent_loop_first(self) -> None:
        assert (
            planner_route_policy.resolve_planner_route(
                entry_action="ask_about_this",
                history_messages=[],
                attachments=[
                    ReaderAskAttachment(
                        kind="record_ref",
                        subtype="related_record",
                        label="Other Article",
                        metadata=ReaderAskAttachmentMetadata(source_surface="reader_page"),
                    )
                ],
                anchors=[],
                cross_record_toggle=False,
                latest_user_message="对照我之前那篇",
            )
            == "agent_loop_first"
        )

    def test_analysis_ref_returns_agent_loop_first(self) -> None:
        assert (
            planner_route_policy.resolve_planner_route(
                entry_action="ask_about_this",
                history_messages=[],
                attachments=[
                    ReaderAskAttachment(
                        kind="analysis_ref",
                        subtype="summary",
                        label="Analysis",
                        metadata=ReaderAskAttachmentMetadata(source_surface="reader_page"),
                    )
                ],
                anchors=[],
                cross_record_toggle=False,
                latest_user_message="解释一下",
            )
            == "agent_loop_first"
        )

    def test_supplement_ref_returns_agent_loop_first(self) -> None:
        assert (
            planner_route_policy.resolve_planner_route(
                entry_action="ask_about_this",
                history_messages=[],
                attachments=[
                    ReaderAskAttachment(
                        kind="supplement_ref",
                        subtype="grammar_note",
                        label="Note",
                        metadata=ReaderAskAttachmentMetadata(source_surface="reader_page"),
                    )
                ],
                anchors=[],
                cross_record_toggle=False,
                latest_user_message="解释一下",
            )
            == "agent_loop_first"
        )

    def test_multiple_external_attachments_agent_loop_first(self) -> None:
        assert (
            planner_route_policy.resolve_planner_route(
                entry_action="ask_about_this",
                history_messages=[],
                attachments=[
                    ReaderAskAttachment(
                        kind="record_ref",
                        subtype="related_record",
                        label="Article 1",
                        metadata=ReaderAskAttachmentMetadata(source_surface="reader_page"),
                    ),
                    ReaderAskAttachment(
                        kind="analysis_ref",
                        subtype="summary",
                        label="Analysis 1",
                        metadata=ReaderAskAttachmentMetadata(source_surface="reader_page"),
                    ),
                ],
                anchors=[],
                cross_record_toggle=False,
                latest_user_message="对比一下",
            )
            == "agent_loop_first"
        )


# ---------------------------------------------------------------------------
# 2. has_explicit_external_attachments is public API
# ---------------------------------------------------------------------------


class TestHasExplicitExternalAttachmentsPublic:
    """Verify has_explicit_external_attachments is a public, callable API."""

    def test_function_is_callable(self) -> None:
        assert callable(planner_route_policy.has_explicit_external_attachments)

    def test_returns_true_for_record_ref_related_record(self) -> None:
        atts = [
            ReaderAskAttachment(
                kind="record_ref",
                subtype="related_record",
                label="att",
                metadata=ReaderAskAttachmentMetadata(source_surface="reader_page"),
            )
        ]
        assert planner_route_policy.has_explicit_external_attachments(atts) is True

    def test_returns_false_for_record_ref_current_record(self) -> None:
        """record_ref/current_record is NOT external — it's the current record."""
        atts = [
            ReaderAskAttachment(
                kind="record_ref",
                subtype="current_record",
                label="att",
                metadata=ReaderAskAttachmentMetadata(source_surface="reader_page"),
            )
        ]
        assert planner_route_policy.has_explicit_external_attachments(atts) is False

    def test_returns_true_for_analysis_ref_without_current_record_id(self) -> None:
        """Without current_record_id, analysis_ref is conservatively treated as external."""
        atts = [
            ReaderAskAttachment(
                kind="analysis_ref",
                subtype="summary",
                label="att",
                metadata=ReaderAskAttachmentMetadata(source_surface="reader_page"),
            )
        ]
        assert planner_route_policy.has_explicit_external_attachments(atts) is True

    def test_returns_false_for_analysis_ref_on_current_record(self) -> None:
        """analysis_ref pointing to current_record_id is NOT external."""
        current_id = str(uuid4())
        atts = [
            ReaderAskAttachment(
                kind="analysis_ref",
                subtype="summary",
                label="att",
                metadata=ReaderAskAttachmentMetadata(
                    source_surface="reader_page",
                    record_id=current_id,
                ),
            )
        ]
        assert (
            planner_route_policy.has_explicit_external_attachments(
                atts, current_record_id=current_id
            )
            is False
        )

    def test_returns_true_for_analysis_ref_on_other_record(self) -> None:
        """analysis_ref pointing to a different record IS external."""
        current_id = str(uuid4())
        other_id = str(uuid4())
        atts = [
            ReaderAskAttachment(
                kind="analysis_ref",
                subtype="summary",
                label="att",
                metadata=ReaderAskAttachmentMetadata(
                    source_surface="reader_page",
                    record_id=other_id,
                ),
            )
        ]
        assert (
            planner_route_policy.has_explicit_external_attachments(
                atts, current_record_id=current_id
            )
            is True
        )

    def test_returns_true_for_supplement_ref_without_current_record_id(self) -> None:
        """Without current_record_id, supplement_ref is conservatively treated as external."""
        atts = [
            ReaderAskAttachment(
                kind="supplement_ref",
                subtype="grammar_note",
                label="att",
                metadata=ReaderAskAttachmentMetadata(source_surface="reader_page"),
            )
        ]
        assert planner_route_policy.has_explicit_external_attachments(atts) is True

    def test_returns_false_for_supplement_ref_on_current_record(self) -> None:
        """supplement_ref pointing to current_record_id is NOT external."""
        current_id = str(uuid4())
        atts = [
            ReaderAskAttachment(
                kind="supplement_ref",
                subtype="grammar_note",
                label="att",
                metadata=ReaderAskAttachmentMetadata(
                    source_surface="reader_page",
                    record_id=current_id,
                ),
            )
        ]
        assert (
            planner_route_policy.has_explicit_external_attachments(
                atts, current_record_id=current_id
            )
            is False
        )

    def test_returns_false_for_text_selection(self) -> None:
        atts = [
            ReaderAskAttachment(
                kind="text_selection",
                subtype="highlight",
                label="att",
                metadata=ReaderAskAttachmentMetadata(source_surface="reader_page"),
            )
        ]
        assert planner_route_policy.has_explicit_external_attachments(atts) is False

    def test_returns_false_for_empty_list(self) -> None:
        assert planner_route_policy.has_explicit_external_attachments([]) is False


# ---------------------------------------------------------------------------
# 3. build_agent_loop_context sets external_attachment_hint
# ---------------------------------------------------------------------------


class TestExternalAttachmentHint:
    """Verify that build_agent_loop_context sets the hint on runtime_state."""

    def _make_record(self) -> MagicMock:
        record = MagicMock()
        record.record_id = uuid4()
        record.title = "Test Record"
        record.render_scene = {}
        record.page_state_json = {}
        return record

    def test_hint_set_when_external_attachment_present(self) -> None:
        from app.services.reader_ask.context_runtime import build_agent_loop_context

        record = self._make_record()
        runtime_state = ReaderAskRuntimeState()
        atts = [
            ReaderAskAttachment(
                kind="record_ref",
                subtype="related_record",
                label="Other Article",
                metadata=ReaderAskAttachmentMetadata(source_surface="reader_page"),
            )
        ]

        with patch("app.services.reader_ask.context_runtime.planner.build_resolved_context_input", return_value={}):
            build_agent_loop_context(
                record=record,
                runtime_state=runtime_state,
                anchors=[],
                attachments=atts,
                user_id=uuid4(),
                page_identity=MagicMock(),
                entry_action="ask_about_this",
                latest_user_message="对照我之前那篇",
            )

        assert runtime_state.external_attachment_hint is not None
        assert "外部引用" in runtime_state.external_attachment_hint
        assert "load_explicit_attachment_context" in runtime_state.external_attachment_hint

    def test_hint_not_set_when_no_external_attachment(self) -> None:
        from app.services.reader_ask.context_runtime import build_agent_loop_context

        record = self._make_record()
        runtime_state = ReaderAskRuntimeState()

        with patch("app.services.reader_ask.context_runtime.planner.build_resolved_context_input", return_value={}):
            build_agent_loop_context(
                record=record,
                runtime_state=runtime_state,
                anchors=[],
                attachments=[],
                user_id=uuid4(),
                page_identity=MagicMock(),
                entry_action="ask_about_this",
                latest_user_message="解释一下",
            )

        assert runtime_state.external_attachment_hint is None

    def test_hint_not_set_for_current_record_ref(self) -> None:
        """record_ref/current_record should NOT trigger external_attachment_hint."""
        from app.services.reader_ask.context_runtime import build_agent_loop_context

        record = self._make_record()
        runtime_state = ReaderAskRuntimeState()
        atts = [
            ReaderAskAttachment(
                kind="record_ref",
                subtype="current_record",
                label="Current Article",
                metadata=ReaderAskAttachmentMetadata(source_surface="reader_page"),
            )
        ]

        with patch("app.services.reader_ask.context_runtime.planner.build_resolved_context_input", return_value={}):
            build_agent_loop_context(
                record=record,
                runtime_state=runtime_state,
                anchors=[],
                attachments=atts,
                user_id=uuid4(),
                page_identity=MagicMock(),
                entry_action="ask_about_this",
                latest_user_message="解释一下",
            )

        assert runtime_state.external_attachment_hint is None


# ---------------------------------------------------------------------------
# 4. external_attachment_hint flows to prompt payload
# ---------------------------------------------------------------------------


class TestExternalAttachmentHintInPayload:
    """Verify external_attachment_hint is included in the prompt payload."""

    def _make_contract(
        self,
        *,
        external_attachment_hint: str | None = None,
        attachments: list[ReaderAskAttachment] | None = None,
    ):
        from app.services.reader_ask.runtime_contract import ReaderAskAnswerRuntimeInput
        from app.schemas.reader_ask import ReaderAskPageIdentity

        record = MagicMock()
        record.record_id = uuid4()
        record.title = "Test"
        record.workflow_version = "1"
        record.schema_version = "1"
        contract_attachments = attachments if attachments is not None else [
            ReaderAskAttachment(
                kind="record_ref",
                subtype="related_record",
                label="Other Article",
                metadata=ReaderAskAttachmentMetadata(source_surface="reader_page"),
            )
        ]

        return ReaderAskAnswerRuntimeInput(
            thread={"id": "t-1", "record_id": "r-1", "title": "Test"},
            record=record,
            user_message="对照我之前那篇",
            history_messages=[],
            page_identity=ReaderAskPageIdentity(
                record_id="r-1",
                title="Test",
                available_context_capabilities=["record_context"],
                has_article_overview=True,
                has_sentence_entries=True,
                has_annotations=False,
                has_reader_notes=False,
            ),
            attachments=contract_attachments,
            anchors=[],
            resolved_intent="explain",
            resolved_intent_label="Explain",
            entry_action="ask_about_this",
            submission_mode="chat",
            cross_record_context_allowed=False,
            resolved_context_input=None,
            quick_action_annotation=None,
            reference_resolution=None,
            planning_snapshot=None,
            max_history_messages=10,
            max_message_text=800,
            external_attachment_hint=external_attachment_hint,
        )

    def test_external_attachment_hint_in_payload(self) -> None:
        from app.services.reader_ask.runtime_contract import build_prompt_payload

        contract = self._make_contract(
            external_attachment_hint="请调用 load_explicit_attachment_context(record_id, asset_id) 加载具体内容"
        )
        payload = build_prompt_payload(contract)
        assert payload["external_attachment_hint"] is not None
        assert "load_explicit_attachment_context" in payload["external_attachment_hint"]

    def test_external_attachment_hint_none_in_payload(self) -> None:
        from app.services.reader_ask.runtime_contract import build_prompt_payload

        contract = self._make_contract(external_attachment_hint=None)
        payload = build_prompt_payload(contract)
        assert payload["external_attachment_hint"] is None

    def test_attachment_manifest_in_payload(self) -> None:
        """Verify attachment metadata (the manifest) is in the payload."""
        from app.services.reader_ask.runtime_contract import build_prompt_payload

        contract = self._make_contract(external_attachment_hint=None)
        payload = build_prompt_payload(contract)
        attachments = payload["canonical_context"]["attachments"]
        assert len(attachments) == 1
        assert attachments[0]["kind"] == "record_ref"

    def test_record_ref_payload_uses_metadata_asset_id_as_tool_record_id(self) -> None:
        from app.services.reader_ask.runtime_contract import build_prompt_payload

        target_record_id = str(uuid4())
        contract = self._make_contract(
            attachments=[
                ReaderAskAttachment(
                    kind="record_ref",
                    subtype="related_record",
                    label="Other Article",
                    metadata=ReaderAskAttachmentMetadata(
                        source_surface="reader_page",
                        asset_id=target_record_id,
                    ),
                )
            ]
        )
        payload = build_prompt_payload(contract)
        att = payload["canonical_context"]["attachments"][0]
        assert att["tool_record_id"] == target_record_id
        assert att["tool_asset_id"] == ""

    def test_analysis_ref_payload_uses_record_id_and_asset_id_pair(self) -> None:
        from app.services.reader_ask.runtime_contract import build_prompt_payload

        target_record_id = str(uuid4())
        target_asset_id = "analysis-1"
        contract = self._make_contract(
            attachments=[
                ReaderAskAttachment(
                    kind="analysis_ref",
                    subtype="sentence_analysis",
                    label="Analysis",
                    metadata=ReaderAskAttachmentMetadata(
                        source_surface="reader_page",
                        record_id=target_record_id,
                        asset_id=target_asset_id,
                    ),
                )
            ]
        )
        payload = build_prompt_payload(contract)
        att = payload["canonical_context"]["attachments"][0]
        assert att["tool_record_id"] == target_record_id
        assert att["tool_asset_id"] == target_asset_id


class TestExternalAttachmentAllowlistManifest:
    """Verify the service allowlist uses the same tool id projection."""

    def test_record_ref_manifest_uses_metadata_asset_id_as_record_fallback(self) -> None:
        from app.services.reader_ask.service import _build_allowed_external_attachments

        target_record_id = str(uuid4())
        manifest = _build_allowed_external_attachments([
            ReaderAskAttachment(
                kind="record_ref",
                subtype="related_record",
                label="Other Article",
                metadata=ReaderAskAttachmentMetadata(
                    source_surface="reader_page",
                    asset_id=target_record_id,
                ),
            )
        ])

        assert manifest == [{"tool_record_id": target_record_id, "tool_asset_id": ""}]

    def test_record_ref_manifest_supports_record_target_key(self) -> None:
        from app.services.reader_ask.service import _build_allowed_external_attachments

        target_record_id = str(uuid4())
        manifest = _build_allowed_external_attachments([
            ReaderAskAttachment(
                kind="record_ref",
                subtype="related_record",
                label="Other Article",
                target_key=f"record:{target_record_id}:record",
                metadata=ReaderAskAttachmentMetadata(source_surface="reader_page"),
            )
        ])

        assert manifest == [{"tool_record_id": target_record_id, "tool_asset_id": ""}]

    def test_analysis_ref_manifest_keeps_asset_id_separate(self) -> None:
        from app.services.reader_ask.service import _build_allowed_external_attachments

        target_record_id = str(uuid4())
        manifest = _build_allowed_external_attachments([
            ReaderAskAttachment(
                kind="analysis_ref",
                subtype="sentence_analysis",
                label="Analysis",
                metadata=ReaderAskAttachmentMetadata(
                    source_surface="reader_page",
                    record_id=target_record_id,
                    asset_id="analysis-1",
                ),
            )
        ])

        assert manifest == [
            {"tool_record_id": target_record_id, "tool_asset_id": "analysis-1"}
        ]


# ---------------------------------------------------------------------------
# 5. load_explicit_attachment_context tool is agent-callable
# ---------------------------------------------------------------------------


class TestLoadExplicitAttachmentContextTool:
    """Verify the new tool is properly registered and agent-callable."""

    def test_tool_in_registry(self) -> None:
        from app.agents.reader_ask_tool_registry import READER_ASK_TOOL_REGISTRY

        assert "load_explicit_attachment_context" in READER_ASK_TOOL_REGISTRY

    def test_tool_is_agent_callable(self) -> None:
        from app.agents.reader_ask_tool_registry import agent_callable_tool_names

        assert "load_explicit_attachment_context" in agent_callable_tool_names()

    def test_tool_not_reserved(self) -> None:
        from app.agents.reader_ask_tool_registry import RESERVED_TOOL_NAMES

        assert "load_explicit_attachment_context" not in RESERVED_TOOL_NAMES

    def test_tool_category_is_context(self) -> None:
        from app.agents.reader_ask_tool_registry import READER_ASK_TOOL_REGISTRY

        spec = READER_ASK_TOOL_REGISTRY["load_explicit_attachment_context"]
        assert spec.category == "context"
        assert spec.effect == "read"

    def test_runtime_state_has_external_attachment_hint(self) -> None:
        state = ReaderAskRuntimeState()
        assert hasattr(state, "external_attachment_hint")
        assert state.external_attachment_hint is None

    def test_agent_deps_has_load_fn(self) -> None:
        from app.agents.reader_ask_agent import ReaderAskAgentDeps

        assert hasattr(ReaderAskAgentDeps, "load_explicit_attachment_context_fn")


class TestLoadExplicitAttachmentContextAllowlist:
    """Verify allowlist enforcement happens before the service loader runs."""

    def test_empty_allowlist_returns_forbidden_without_calling_loader(self) -> None:
        ctx, loader, _state = _make_loader_ctx(allowed_external_attachments=[])

        result = asyncio.run(
            _load_explicit_attachment_context_tool(
                ctx,
                record_id="00000000-0000-0000-0000-000000000002",
            )
        )

        assert result["status"] == "forbidden"
        assert result["ok"] is False
        loader.assert_not_awaited()

    def test_record_only_allowlist_does_not_allow_asset_load(self) -> None:
        ctx, loader, _state = _make_loader_ctx(
            allowed_external_attachments=[
                {
                    "tool_record_id": "00000000-0000-0000-0000-000000000002",
                    "tool_asset_id": "",
                }
            ]
        )

        result = asyncio.run(
            _load_explicit_attachment_context_tool(
                ctx,
                record_id="00000000-0000-0000-0000-000000000002",
                asset_id="asset-1",
            )
        )

        assert result["status"] == "forbidden"
        assert result["ok"] is False
        loader.assert_not_awaited()

    def test_asset_allowlist_does_not_allow_record_only_load(self) -> None:
        ctx, loader, _state = _make_loader_ctx(
            allowed_external_attachments=[
                {
                    "tool_record_id": "00000000-0000-0000-0000-000000000002",
                    "tool_asset_id": "asset-1",
                }
            ]
        )

        result = asyncio.run(
            _load_explicit_attachment_context_tool(
                ctx,
                record_id="00000000-0000-0000-0000-000000000002",
            )
        )

        assert result["status"] == "forbidden"
        assert result["ok"] is False
        loader.assert_not_awaited()

    def test_record_only_allowlist_loads_record_and_writes_state(self) -> None:
        ctx, loader, state = _make_loader_ctx(
            allowed_external_attachments=[
                {
                    "tool_record_id": "00000000-0000-0000-0000-000000000002",
                    "tool_asset_id": "",
                }
            ],
            loader_result={
                "status": "loaded",
                "record_id": "00000000-0000-0000-0000-000000000002",
                "record_title": "External",
                "article_overview": "Overview",
                "record_insights": [],
                "source_labels": ["external_attachment"],
                "ok": True,
            },
        )

        result = asyncio.run(
            _load_explicit_attachment_context_tool(
                ctx,
                record_id="00000000-0000-0000-0000-000000000002",
            )
        )

        assert result["status"] == "loaded"
        loader.assert_awaited_once()
        assert state.used_cross_record_context is True
        assert state.latest_external_record_contexts[0]["record_id"] == (
            "00000000-0000-0000-0000-000000000002"
        )

    def test_asset_allowlist_loads_asset_and_writes_state(self) -> None:
        ctx, loader, state = _make_loader_ctx(
            allowed_external_attachments=[
                {
                    "tool_record_id": "00000000-0000-0000-0000-000000000002",
                    "tool_asset_id": "asset-1",
                }
            ],
            loader_result={
                "status": "loaded",
                "record_id": "00000000-0000-0000-0000-000000000002",
                "record_title": "External",
                "asset_id": "asset-1",
                "asset_type": "analysis",
                "entry_type": "sentence_analysis",
                "asset_title": "Sentence analysis",
                "content_md": "Analysis body",
                "source_labels": ["external_attachment", "external_assets"],
                "ok": True,
            },
        )

        result = asyncio.run(
            _load_explicit_attachment_context_tool(
                ctx,
                record_id="00000000-0000-0000-0000-000000000002",
                asset_id="asset-1",
            )
        )

        assert result["status"] == "loaded"
        loader.assert_awaited_once()
        assert state.used_cross_record_context is True
        assert state.latest_external_asset_contexts[0]["asset_id"] == "asset-1"


# ---------------------------------------------------------------------------
# 6. planner_first fallbacks preserved (dictionary, long history)
# ---------------------------------------------------------------------------


class TestPlannerFirstFallbacksPreserved:
    """Verify that dictionary and long-history fallbacks are still intact."""

    def test_dictionary_anchor_fallback(self) -> None:
        dict_anchor = ReaderAskAnchorRef(
            anchor_type="dictionary_entry", label="dict", dict_entry_id=1
        )
        route = planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=[],
            attachments=[],
            anchors=[dict_anchor],
            cross_record_toggle=False,
            latest_user_message="这个词什么意思",
        )
        assert route == "planner_first"

    def test_dictionary_attachment_fallback(self) -> None:
        dict_att = ReaderAskAttachment(
            kind="text_selection",
            subtype="dictionary_entry",
            label="dict",
            metadata=ReaderAskAttachmentMetadata(source_surface="reader_page"),
        )
        route = planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=[],
            attachments=[dict_att],
            anchors=[],
            cross_record_toggle=False,
            latest_user_message="这个词什么意思",
        )
        assert route == "planner_first"

    def test_long_history_fallback(self) -> None:
        history = [{"role": "user", "content_md": f"msg {i}"} for i in range(11)]
        route = planner_route_policy.resolve_planner_route(
            entry_action="ask_about_this",
            history_messages=history,
            attachments=[],
            anchors=[],
            cross_record_toggle=False,
            latest_user_message="继续",
        )
        assert route == "planner_first"


# ---------------------------------------------------------------------------
# 7. Tool registry invariants hold with new tool
# ---------------------------------------------------------------------------


class TestToolRegistryInvariants:
    """Verify that adding the new tool doesn't break registry invariants."""

    def test_registry_invariants_hold(self) -> None:
        from app.agents.reader_ask_tool_registry import assert_registry_invariants

        # Should not raise
        assert_registry_invariants()

    def test_agent_callable_count(self) -> None:
        from app.agents.reader_ask_tool_registry import agent_callable_tool_names

        names = agent_callable_tool_names()
        # 8 original + 1 new = 9 agent-callable tools
        assert len(names) == 9
        assert "load_explicit_attachment_context" in names
