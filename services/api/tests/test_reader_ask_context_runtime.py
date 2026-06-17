"""Tests for reader_ask context_runtime: context materialization and external asset loading."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.agents.reader_ask_agent import ReaderAskRuntimeState
from app.services.reader_ask import context_runtime as context_runtime_svc


# ---------------------------------------------------------------------------
# render_scene_article_overview
# ---------------------------------------------------------------------------

class TestRenderSceneArticleOverview:
    def test_returns_overview_when_present(self) -> None:
        record = MagicMock()
        record.render_scene = {"content_summary": {"overview": "This is the article overview."}}
        record.page_state_json = {}
        result = context_runtime_svc.render_scene_article_overview(record)
        assert result == "This is the article overview."

    def test_returns_none_when_no_overview(self) -> None:
        record = MagicMock()
        record.render_scene = {}
        record.page_state_json = {}
        result = context_runtime_svc.render_scene_article_overview(record)
        assert result is None

    def test_returns_none_for_empty_overview(self) -> None:
        record = MagicMock()
        record.render_scene = {}
        record.page_state_json = {"overview": "  "}
        result = context_runtime_svc.render_scene_article_overview(record)
        assert result is None


# ---------------------------------------------------------------------------
# current_record_source_labels
# ---------------------------------------------------------------------------

class TestCurrentRecordSourceLabels:
    def test_empty_state(self) -> None:
        state = ReaderAskRuntimeState()
        labels = context_runtime_svc.current_record_source_labels(state)
        assert labels == []

    def test_with_record_context(self) -> None:
        state = ReaderAskRuntimeState()
        state.latest_record_context = {"paragraphs": []}
        labels = context_runtime_svc.current_record_source_labels(state)
        assert "current_paragraph" in labels

    def test_with_insights(self) -> None:
        state = ReaderAskRuntimeState()
        state.latest_record_insights = [{"insight": "test"}]
        labels = context_runtime_svc.current_record_source_labels(state)
        assert "record_assets" in labels

    def test_with_overview(self) -> None:
        state = ReaderAskRuntimeState()
        state.latest_article_overview = "An overview"
        labels = context_runtime_svc.current_record_source_labels(state)
        assert "article_overview" in labels


# ---------------------------------------------------------------------------
# external_context_has_structured_assets / external_asset_context_has_items
# ---------------------------------------------------------------------------

class TestExternalContextHelpers:
    def test_structured_assets_none(self) -> None:
        assert context_runtime_svc.external_context_has_structured_assets(None) is False

    def test_structured_assets_empty(self) -> None:
        assert context_runtime_svc.external_context_has_structured_assets([]) is False

    def test_structured_assets_with_overview(self) -> None:
        items = [{"article_overview": "overview text"}]
        assert context_runtime_svc.external_context_has_structured_assets(items) is True

    def test_structured_assets_with_insights(self) -> None:
        items = [{"record_insights": [{"insight": "test"}]}]
        assert context_runtime_svc.external_context_has_structured_assets(items) is True

    def test_asset_items_none(self) -> None:
        assert context_runtime_svc.external_asset_context_has_items(None) is False

    def test_asset_items_with_asset_id(self) -> None:
        items = [{"asset_id": "a1"}]
        assert context_runtime_svc.external_asset_context_has_items(items) is True

    def test_asset_items_without_asset_id(self) -> None:
        items = [{"record_id": "r1"}]
        assert context_runtime_svc.external_asset_context_has_items(items) is False


# ---------------------------------------------------------------------------
# load_external_asset_contexts
# ---------------------------------------------------------------------------

class TestLoadExternalAssetContexts:
    def test_empty_list(self) -> None:
        result = context_runtime_svc.load_external_asset_contexts(
            current_record_id=uuid4(),
            planned_external_assets=[],
        )
        assert result == []

    def test_skips_current_record(self) -> None:
        current = uuid4()
        result = context_runtime_svc.load_external_asset_contexts(
            current_record_id=current,
            planned_external_assets=[
                {"record_id": str(current), "asset_id": "a1"},
            ],
        )
        assert result == []

    def test_rejects_invalid_uuid(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            context_runtime_svc.load_external_asset_contexts(
                current_record_id=uuid4(),
                planned_external_assets=[
                    {"record_id": "not-a-uuid", "asset_id": "a1"},
                ],
            )
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "external asset record id is invalid"

    def test_loads_valid_asset(self) -> None:
        other = uuid4()
        result = context_runtime_svc.load_external_asset_contexts(
            current_record_id=uuid4(),
            planned_external_assets=[
                {
                    "record_id": str(other),
                    "asset_id": "a1",
                    "record_title": "Other Record",
                    "asset_type": "analysis",
                    "asset_title": "My Asset",
                    "content_md": "Some content",
                    "content_summary": "Summary",
                    "source_labels": ["external_assets"],
                    "reason": "explicit_attachment",
                },
            ],
        )
        assert len(result) == 1
        assert result[0].record_id == str(other)
        assert result[0].asset_id == "a1"
        assert result[0].asset_title == "My Asset"

    def test_deduplicates(self) -> None:
        other = uuid4()
        result = context_runtime_svc.load_external_asset_contexts(
            current_record_id=uuid4(),
            planned_external_assets=[
                {"record_id": str(other), "asset_id": "a1"},
                {"record_id": str(other), "asset_id": "a1"},
            ],
        )
        assert len(result) == 1


# ---------------------------------------------------------------------------
# load_external_record_contexts
# ---------------------------------------------------------------------------

class TestLoadExternalRecordContexts:
    @pytest.mark.asyncio
    async def test_empty_refs(self) -> None:
        load_cb = AsyncMock()
        result = await context_runtime_svc.load_external_record_contexts(
            uuid4(),
            current_record_id=uuid4(),
            planned_external_refs=[],
            load_record_bundle_cb=load_cb,
        )
        assert result == []
        load_cb.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_current_record(self) -> None:
        current = uuid4()
        load_cb = AsyncMock()
        result = await context_runtime_svc.load_external_record_contexts(
            uuid4(),
            current_record_id=current,
            planned_external_refs=[{"record_id": str(current)}],
            load_record_bundle_cb=load_cb,
        )
        assert result == []
        load_cb.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_invalid_uuid(self) -> None:
        load_cb = AsyncMock()
        with pytest.raises(HTTPException) as exc_info:
            await context_runtime_svc.load_external_record_contexts(
                uuid4(),
                current_record_id=uuid4(),
                planned_external_refs=[{"record_id": "not-a-uuid"}],
                load_record_bundle_cb=load_cb,
            )
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "external record id is invalid"
        load_cb.assert_not_called()

    @pytest.mark.asyncio
    async def test_loads_valid_record(self) -> None:
        user_id = uuid4()
        other = uuid4()
        current = uuid4()
        mock_bundle = MagicMock()
        mock_bundle.record_id = other
        mock_bundle.title = "Other Record"
        mock_bundle.render_scene = {"article_overview": "Overview text"}
        mock_bundle.page_state_json = {}
        load_cb = AsyncMock(return_value=mock_bundle)

        with patch.object(
            context_runtime_svc.resolver_svc,
            "lookup_structured_record_assets",
            return_value={
                "record_id": str(other),
                "record_title": "Other Record",
                "article_overview": "Overview text",
                "article_overview_status": "completed",
                "article_overview_source": "ai_generated",
                "article_overview_confidence": "high",
                "record_insights": ["key insight 1", "key insight 2"],
                "source_labels": ["external_record_context"],
                "reason": "explicit_attachment",
            },
        ):
            result = await context_runtime_svc.load_external_record_contexts(
                user_id,
                current_record_id=current,
                planned_external_refs=[{"record_id": str(other)}],
                load_record_bundle_cb=load_cb,
            )

        assert len(result) == 1
        assert result[0].record_id == str(other)
        assert result[0].record_title == "Other Record"
        load_cb.assert_called_once_with(user_id, other)


# ---------------------------------------------------------------------------
# materialize_planned_context
# ---------------------------------------------------------------------------

class TestMaterializePlannedContext:
    def _make_record(self, *, record_id: UUID | None = None) -> MagicMock:
        rid = record_id or uuid4()
        record = MagicMock()
        record.record_id = rid
        record.title = "Test Record"
        record.render_scene = {}
        record.page_state_json = {"overview": "Test overview"}
        record.source_text = "Test source"
        record.workflow_version = None
        record.schema_version = None
        return record

    def _make_planning_snapshot(
        self,
        *,
        local_context_needed: bool = True,
        insights_needed: bool = True,
        overview_needed: bool = True,
        external_record_refs: list[dict[str, str]] | None = None,
        external_asset_refs: list[dict[str, object]] | None = None,
    ) -> MagicMock:
        working_set = MagicMock()
        working_set.local_context_window_needed = local_context_needed
        working_set.record_insights_needed = insights_needed
        working_set.article_overview_needed = overview_needed
        working_set.external_record_refs = external_record_refs or []
        working_set.external_asset_refs = external_asset_refs or []
        snap = MagicMock()
        snap.working_set = working_set
        return snap

    @pytest.mark.asyncio
    async def test_current_record_context_materialization(self) -> None:
        """When working_set requests local context, it should be loaded via callback."""
        record = self._make_record()
        snap = self._make_planning_snapshot(local_context_needed=True, insights_needed=False, overview_needed=False)
        runtime_state = ReaderAskRuntimeState()

        get_context_cb = AsyncMock(return_value={"paragraphs": ["p1"]})
        get_insights_cb = AsyncMock(return_value=[])
        load_bundle_cb = AsyncMock()

        with patch.object(
            context_runtime_svc.planner, "build_resolved_context_input",
            return_value={"context": "assembled"},
        ):
            result = await context_runtime_svc.materialize_planned_context(
                user_id=uuid4(),
                record=record,
                runtime_state=runtime_state,
                planning_snapshot=snap,
                page_identity=MagicMock(),
                entry_action="ask_about_this",
                attachments=[],
                anchors=[],
                get_record_context_cb=get_context_cb,
                get_record_insights_cb=get_insights_cb,
                load_record_bundle_cb=load_bundle_cb,
            )

        assert result == {"context": "assembled"}
        get_context_cb.assert_called_once()
        assert runtime_state.latest_record_context == {"paragraphs": ["p1"]}
        assert "current_paragraph" in runtime_state.source_labels

    @pytest.mark.asyncio
    async def test_record_insights_filled(self) -> None:
        """When working_set requests insights, they should be loaded via callback."""
        record = self._make_record()
        snap = self._make_planning_snapshot(local_context_needed=False, insights_needed=True, overview_needed=False)
        runtime_state = ReaderAskRuntimeState()

        get_context_cb = AsyncMock()
        get_insights_cb = AsyncMock(return_value=[{"insight": "key point"}])
        load_bundle_cb = AsyncMock()

        with patch.object(
            context_runtime_svc.planner, "build_resolved_context_input",
            return_value={"context": "assembled"},
        ):
            await context_runtime_svc.materialize_planned_context(
                user_id=uuid4(),
                record=record,
                runtime_state=runtime_state,
                planning_snapshot=snap,
                page_identity=MagicMock(),
                entry_action="ask_about_this",
                attachments=[],
                anchors=[],
                get_record_context_cb=get_context_cb,
                get_record_insights_cb=get_insights_cb,
                load_record_bundle_cb=load_bundle_cb,
            )

        get_insights_cb.assert_called_once()
        assert runtime_state.latest_record_insights == [{"insight": "key point"}]
        assert "record_assets" in runtime_state.source_labels

    @pytest.mark.asyncio
    async def test_noop_when_nothing_needed(self) -> None:
        """When working_set needs nothing, callbacks should not be called."""
        record = self._make_record()
        snap = self._make_planning_snapshot(local_context_needed=False, insights_needed=False, overview_needed=False)
        runtime_state = ReaderAskRuntimeState()

        get_context_cb = AsyncMock()
        get_insights_cb = AsyncMock()
        load_bundle_cb = AsyncMock()

        with patch.object(
            context_runtime_svc.planner, "build_resolved_context_input",
            return_value={"context": "assembled"},
        ):
            result = await context_runtime_svc.materialize_planned_context(
                user_id=uuid4(),
                record=record,
                runtime_state=runtime_state,
                planning_snapshot=snap,
                page_identity=MagicMock(),
                entry_action="ask_about_this",
                attachments=[],
                anchors=[],
                get_record_context_cb=get_context_cb,
                get_record_insights_cb=get_insights_cb,
                load_record_bundle_cb=load_bundle_cb,
            )

        get_context_cb.assert_not_called()
        get_insights_cb.assert_not_called()
        load_bundle_cb.assert_not_called()
        assert result == {"context": "assembled"}

    @pytest.mark.asyncio
    async def test_attachment_missing_does_not_break(self) -> None:
        """Empty external refs should not break the resolved_context_input."""
        record = self._make_record()
        snap = self._make_planning_snapshot(
            local_context_needed=False,
            insights_needed=False,
            overview_needed=False,
            external_record_refs=[],
            external_asset_refs=[],
        )
        runtime_state = ReaderAskRuntimeState()

        with patch.object(
            context_runtime_svc.planner, "build_resolved_context_input",
            return_value={"context": "assembled"},
        ):
            result = await context_runtime_svc.materialize_planned_context(
                user_id=uuid4(),
                record=record,
                runtime_state=runtime_state,
                planning_snapshot=snap,
                page_identity=MagicMock(),
                entry_action="ask_about_this",
                attachments=[],
                anchors=[],
                get_record_context_cb=AsyncMock(),
                get_record_insights_cb=AsyncMock(),
                load_record_bundle_cb=AsyncMock(),
            )

        assert result == {"context": "assembled"}
        assert not runtime_state.used_cross_record_context


# ---------------------------------------------------------------------------
# materialize_planned_context with planning_snapshot=None (agent-loop-first safety)
# ---------------------------------------------------------------------------


class TestMaterializeMinimalContext:
    """When `planning_snapshot` is None (agent-loop-first), ``materialize_planned_context``
    must NOT call the record/insights callbacks, must still attempt the
    article_overview, and must return a minimal ``resolved_context_input``
    with empty external lists.
    """

    def _make_record(self) -> MagicMock:
        record = MagicMock()
        record.record_id = uuid4()
        record.title = "Agent Loop First Record"
        record.render_scene = {"content_summary": {"overview": "Article overview from render_scene."}}
        record.page_state_json = {}
        return record

    @pytest.mark.asyncio
    async def test_snapshot_none_skips_record_context_callback(self) -> None:
        record = self._make_record()
        runtime_state = ReaderAskRuntimeState()

        get_context_cb = AsyncMock()
        get_insights_cb = AsyncMock()
        load_bundle_cb = AsyncMock()

        with patch.object(
            context_runtime_svc.planner, "build_resolved_context_input",
            return_value={"context": "minimal"},
        ):
            await context_runtime_svc.materialize_planned_context(
                user_id=uuid4(),
                record=record,
                runtime_state=runtime_state,
                planning_snapshot=None,
                page_identity=MagicMock(),
                entry_action="ask_about_this",
                attachments=[],
                anchors=[],
                get_record_context_cb=get_context_cb,
                get_record_insights_cb=get_insights_cb,
                load_record_bundle_cb=load_bundle_cb,
            )

        get_context_cb.assert_not_awaited()
        get_insights_cb.assert_not_awaited()
        load_bundle_cb.assert_not_awaited()
        assert runtime_state.latest_record_context is None

    @pytest.mark.asyncio
    async def test_snapshot_none_attempts_article_overview(self) -> None:
        record = self._make_record()
        runtime_state = ReaderAskRuntimeState()

        with patch.object(
            context_runtime_svc.planner, "build_resolved_context_input",
            return_value={"context": "minimal"},
        ):
            await context_runtime_svc.materialize_planned_context(
                user_id=uuid4(),
                record=record,
                runtime_state=runtime_state,
                planning_snapshot=None,
                page_identity=MagicMock(),
                entry_action="ask_about_this",
                attachments=[],
                anchors=[],
                get_record_context_cb=AsyncMock(),
                get_record_insights_cb=AsyncMock(),
                load_record_bundle_cb=AsyncMock(),
            )

        assert runtime_state.latest_article_overview == "Article overview from render_scene."

    @pytest.mark.asyncio
    async def test_snapshot_none_builds_minimal_resolved_context(self) -> None:
        record = self._make_record()
        runtime_state = ReaderAskRuntimeState()

        captured: dict[str, object] = {}

        def capture(**kwargs: object) -> dict[str, object]:
            captured.update(kwargs)
            return {"context": "minimal"}

        with patch.object(
            context_runtime_svc.planner, "build_resolved_context_input",
            side_effect=capture,
        ):
            await context_runtime_svc.materialize_planned_context(
                user_id=uuid4(),
                record=record,
                runtime_state=runtime_state,
                planning_snapshot=None,
                page_identity=MagicMock(),
                entry_action="ask_about_this",
                attachments=[],
                anchors=[],
                get_record_context_cb=AsyncMock(),
                get_record_insights_cb=AsyncMock(),
                load_record_bundle_cb=AsyncMock(),
            )

        current_record_context = captured["current_record_context"]
        assert current_record_context.record_id == str(record.record_id)
        assert current_record_context.record_title == record.title
        # ``local_context`` and ``record_insights`` should be empty — either
        # None or an empty list is acceptable since both signal "no data".
        assert current_record_context.local_context in (None, [])
        assert current_record_context.record_insights in (None, [])
        assert current_record_context.article_overview == "Article overview from render_scene."
        assert captured["external_record_contexts"] == []
        assert captured["external_asset_contexts"] == []
