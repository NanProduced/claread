from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from app.api.routes.vocabulary import _vocab_row_to_response
from app.database.json_compat import ensure_json_array, ensure_json_object
from app.services.ai_usage.service import AIUsageEventCreate, record_ai_usage_event
from app.services.credits import LedgerAttribution, reserve_points
from app.services.auth.identity import get_or_create_user_by_identity
from app.services.auth.profile import get_user_profile, update_user_profile
from app.services.daily_reader.pipeline_tracker import PipelineRunTracker
from app.services.dictionary_ai.repository import insert_candidate_entry
from app.services.feedback.service import submit_feedback
from app.services.user_annotations import _row_to_response as annotation_row_to_response
from app.services.user_assets.vocabulary import (
    _merge_payload_on_conflict,
    upsert_vocabulary,
)


def _make_mock_conn_with_tx() -> AsyncMock:
    mock_conn = AsyncMock()
    tx_ctx = MagicMock()
    tx_ctx.__aenter__ = AsyncMock(return_value=None)
    tx_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_conn.transaction = MagicMock(return_value=tx_ctx)
    return mock_conn


def _make_mock_pool(mock_conn: AsyncMock) -> MagicMock:
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_pool


@pytest.fixture
def anyio_backend():
    return "asyncio"


class TestJsonCompatHelpers:
    def test_ensure_json_object_accepts_native_and_legacy_values(self):
        assert ensure_json_object({"a": 1}) == {"a": 1}
        assert ensure_json_object('{"a": 1}') == {"a": 1}
        assert ensure_json_object("[1, 2]") == {}

    def test_ensure_json_array_accepts_native_and_legacy_values(self):
        assert ensure_json_array([1, 2]) == [1, 2]
        assert ensure_json_array("[1, 2]") == [1, 2]
        assert ensure_json_array('{"a": 1}') == []


class TestJsonCompatibilityReads:
    def test_vocabulary_route_parses_legacy_string_json(self):
        now = datetime.now(UTC)
        row = {
            "id": uuid4(),
            "user_id": uuid4(),
            "lemma": "test",
            "display_word": "test",
            "phonetic": None,
            "part_of_speech": None,
            "short_meaning": "meaning",
            "meanings_json": '[{"part_of_speech":"n.","definitions":[]}]',
            "tags": [],
            "exchange": [],
            "source_provider": "tecd3",
            "dict_entry_id": None,
            "source_sentence": None,
            "source_context": None,
            "mastery_status": "new",
            "review_count": 0,
            "last_reviewed_at": None,
            "payload_json": '{"review":{"stage":1}}',
            "created_at": now,
            "updated_at": now,
        }

        response = _vocab_row_to_response(row)

        assert response.meanings_json[0]["part_of_speech"] == "n."
        assert response.payload_json["review"]["stage"] == 1

    def test_annotation_response_accepts_native_payload_json(self):
        now = datetime.now(UTC)
        row = {
            "id": uuid4(),
            "analysis_record_id": uuid4(),
            "anchor_type": "sentence",
            "target_key": "record:r:sentence:s1",
            "paragraph_id": "p1",
            "sentence_id": "s1",
            "selected_text": "Test text",
            "start_offset": None,
            "end_offset": None,
            "text_hash": None,
            "color": "warm_yellow",
            "payload_json": {"segments": []},
            "created_at": now,
            "updated_at": now,
        }

        response = annotation_row_to_response(row)

        assert response.payload_json == {"segments": []}


class TestJsonbWriteContracts:
    def test_merge_payload_on_conflict_dedupes_source_refs_by_reading_record_id(self):
        merged = _merge_payload_on_conflict(
            existing_payload={
                "source_refs": [
                    {
                        "reading_record_id": "reading-record-1",
                        "client_record_id": "legacy-a",
                        "source_sentence_id": "sent-a",
                    }
                ]
            },
            incoming_payload={
                "source_refs": [
                    {
                        "reading_record_id": "reading-record-1",
                        "client_record_id": "legacy-b",
                        "source_sentence_id": "sent-b",
                    },
                    {
                        "client_record_id": "legacy-c",
                        "source_sentence_id": "sent-c",
                    },
                ]
            },
            incoming_display_word="test",
        )

        assert merged["source_refs"] == [
            {
                "reading_record_id": "reading-record-1",
                "client_record_id": "legacy-a",
                "source_sentence_id": "sent-a",
            },
            {
                "client_record_id": "legacy-c",
                "source_sentence_id": "sent-c",
            }
        ]

    @pytest.mark.anyio
    async def test_upsert_vocabulary_writes_native_jsonb(self):
        mock_conn = _make_mock_conn_with_tx()
        mock_conn.fetchrow = AsyncMock(side_effect=[
            None,
            {"id": uuid4(), "updated_at": datetime.now(UTC), "created": True},
        ])
        mock_pool = _make_mock_pool(mock_conn)

        with patch("app.services.user_assets.vocabulary.db_connection.DB_POOL", mock_pool):
            await upsert_vocabulary(
                user_id=uuid4(),
                lemma="test",
                display_word="test",
                short_meaning="meaning",
                dict_entry_id=None,
                phonetic=None,
                part_of_speech=None,
                meanings_json=[{"part_of_speech": "n.", "definitions": []}],
                tags=[],
                exchange=[],
                source_provider="tecd3",
                source_sentence=None,
                source_context=None,
                payload_json={},
            )

        insert_args = mock_conn.fetchrow.await_args_list[1].args
        assert isinstance(insert_args[7], list)
        assert isinstance(insert_args[15], dict)
        assert insert_args[15]["review"]["stage"] == 0

    @pytest.mark.anyio
    async def test_auth_identity_writes_native_auth_payload_json(self):
        user_id = UUID("22222222-2222-4222-8222-222222222222")
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = None
        mock_conn.fetchval.return_value = user_id
        mock_pool = _make_mock_pool(mock_conn)

        with patch("app.services.auth.identity.db_connection.DB_POOL", mock_pool):
            result = await get_or_create_user_by_identity(
                provider="phone",
                provider_user_id="+8613800138000",
                auth_payload={"verified_by": "mock"},
            )

        assert result.user_id == user_id
        insert_args = mock_conn.execute.await_args_list[-1].args
        assert any(isinstance(arg, dict) and arg["verified_by"] == "mock" for arg in insert_args)
    @pytest.mark.anyio
    async def test_ai_usage_event_writes_native_metadata_json(self):
        metadata_json = {"source": "test"}
        mock_conn = AsyncMock()
        mock_conn.fetchval.return_value = uuid4()
        mock_pool = _make_mock_pool(mock_conn)

        with patch("app.services.ai_usage.service.db_connection.DB_POOL", mock_pool):
            await record_ai_usage_event(
                AIUsageEventCreate(
                    usage_scope="user_billed",
                    capability_code="analysis",
                    billing_mode="user_points",
                    status="succeeded",
                    metadata_json=metadata_json,
                )
            )

        fetchval_args = mock_conn.fetchval.await_args.args
        assert any(isinstance(arg, dict) and arg["source"] == "test" for arg in fetchval_args)

    @pytest.mark.anyio
    async def test_ai_usage_event_writes_reader_orchestration_attribution_fields(self):
        reading_record_id = uuid4()
        reader_run_id = uuid4()
        reader_job_id = uuid4()
        enhancement_layer_id = uuid4()
        mock_conn = AsyncMock()
        mock_conn.fetchval.return_value = uuid4()
        mock_pool = _make_mock_pool(mock_conn)

        with patch("app.services.ai_usage.service.db_connection.DB_POOL", mock_pool):
            await record_ai_usage_event(
                AIUsageEventCreate(
                    usage_scope="user_billed",
                    capability_code="reader_translation",
                    billing_mode="user_points",
                    status="succeeded",
                    reading_record_id=reading_record_id,
                    reader_run_id=reader_run_id,
                    reader_job_id=reader_job_id,
                    enhancement_layer_id=enhancement_layer_id,
                    model_profile_id="reader_translation_default",
                    cache_hit=True,
                    cache_status="hit",
                    cache_class="5m",
                    operation_fingerprint="fp-1",
                )
            )

        fetchval_args = mock_conn.fetchval.await_args.args
        assert reading_record_id in fetchval_args
        assert reader_job_id in fetchval_args
        assert enhancement_layer_id in fetchval_args
        assert "reader_translation_default" in fetchval_args
        assert "fp-1" in fetchval_args

    @pytest.mark.anyio
    async def test_credit_service_writes_generic_ledger_attribution_fields(self):
        reading_record_id = uuid4()
        reader_run_id = uuid4()
        reader_job_id = uuid4()
        mock_conn = _make_mock_conn_with_tx()
        mock_conn.fetchrow = AsyncMock(return_value={
            "daily_free_points": 1000,
            "daily_used_points": 0,
            "bonus_points": 0,
            "last_reset_on": date.today(),
        })
        mock_pool = _make_mock_pool(mock_conn)
        user_id = uuid4()

        with patch("app.services.credits.db_connection.DB_POOL", mock_pool):
            await reserve_points(
                user_id,
                5,
                metadata={"capability_code": "reader_translation"},
                attribution=LedgerAttribution(
                    subject_type="reading_record",
                    subject_id=str(reading_record_id),
                    reading_record_id=reading_record_id,
                    reader_run_id=reader_run_id,
                    reader_job_id=reader_job_id,
                    title_snapshot="Test Title",
                ),
            )

        execute_args = mock_conn.execute.await_args_list[-1].args
        assert "reading_record" in execute_args
        assert str(reading_record_id) in execute_args
        assert reading_record_id in execute_args
        assert reader_job_id in execute_args
        assert "Test Title" in execute_args

    @pytest.mark.anyio
    async def test_submit_feedback_writes_native_context_json(self):
        mock_conn = _make_mock_conn_with_tx()
        mock_conn.fetchrow.return_value = {
            "id": uuid4(),
            "feedback_scope": "app",
            "target_id": "app_general",
            "sentiment": "positive",
            "feedback_type": "feature_request",
            "client_platform": "web",
            "client_surface": "settings",
            "entry_point": "settings_form",
            "context_summary": "Settings form",
            "status": "pending",
            "created_at": datetime.now(UTC),
        }
        mock_pool = _make_mock_pool(mock_conn)
        context_json = {"page": "home"}

        with patch("app.services.feedback.service.db_connection.DB_POOL", mock_pool):
            await submit_feedback(
                user_id=uuid4(),
                feedback_scope="app",
                target_id="app_general",
                sentiment="positive",
                feedback_type="feature_request",
                content=None,
                context_json=context_json,
                context_summary="Settings form",
                client_platform="web",
                client_surface="settings",
                entry_point="settings_form",
                app_version=None,
            )

        fetchrow_args = mock_conn.fetchrow.await_args.args
        assert any(isinstance(arg, dict) and arg == context_json for arg in fetchrow_args)

    @pytest.mark.anyio
    async def test_candidate_entry_writes_native_generated_payload_json(self):
        mock_conn = AsyncMock()
        mock_conn.fetchval.return_value = uuid4()
        mock_pool = _make_mock_pool(mock_conn)
        generated_payload_json = {"candidate": {"word": "doomscroll"}}

        with patch("app.services.dictionary_ai.repository.db_connection.DB_POOL", mock_pool):
            await insert_candidate_entry(
                query="doomscrolling",
                normalized_query="doomscrolling",
                query_type="word",
                classification="slang_or_informal",
                result_kind="ai_entry",
                confidence="medium",
                generated_payload_json=generated_payload_json,
                context_sentence="She spent the whole night doomscrolling.",
                sentence_id=None,
                usage_event_id=None,
            )

        fetchval_args = mock_conn.fetchval.await_args.args
        assert any(isinstance(arg, dict) and arg == generated_payload_json for arg in fetchval_args)

    @pytest.mark.anyio
    async def test_candidate_entry_writes_reading_record_anchor_columns(self):
        """C4: insert_candidate_entry must write reading_record_id / base_id / generation."""
        mock_conn = AsyncMock()
        mock_conn.fetchval.return_value = uuid4()
        mock_pool = _make_mock_pool(mock_conn)
        reading_record_id = uuid4()
        base_id = uuid4()
        generation = 3

        with patch("app.services.dictionary_ai.repository.db_connection.DB_POOL", mock_pool):
            await insert_candidate_entry(
                query="doomscrolling",
                normalized_query="doomscrolling",
                query_type="word",
                classification="slang_or_informal",
                result_kind="ai_entry",
                confidence="medium",
                generated_payload_json={"candidate": {"word": "doomscroll"}},
                context_sentence="She spent the whole night doomscrolling.",
                sentence_id="sent-1",
                usage_event_id=None,
                reading_record_id=reading_record_id,
                base_id=base_id,
                generation=generation,
            )

        fetchval_args = mock_conn.fetchval.await_args.args
        # reading_record_id, base_id, generation should appear as positional params ($12, $13, $14)
        assert reading_record_id in fetchval_args
        assert base_id in fetchval_args
        assert generation in fetchval_args

    @pytest.mark.anyio
    async def test_candidate_entry_reading_record_anchor_defaults_to_none(self):
        """C4: insert_candidate_entry must accept calls without reading_record anchor (legacy compat)."""
        mock_conn = AsyncMock()
        mock_conn.fetchval.return_value = uuid4()
        mock_pool = _make_mock_pool(mock_conn)

        with patch("app.services.dictionary_ai.repository.db_connection.DB_POOL", mock_pool):
            await insert_candidate_entry(
                query="doomscrolling",
                normalized_query="doomscrolling",
                query_type="word",
                classification="slang_or_informal",
                result_kind="ai_entry",
                confidence="medium",
                generated_payload_json={"candidate": {"word": "doomscroll"}},
                context_sentence="She spent the whole night doomscrolling.",
                sentence_id=None,
                usage_event_id=None,
            )

        # Should not raise; None values for reading_record_id/base_id/generation are valid
        mock_conn.fetchval.assert_awaited_once()

    @pytest.mark.anyio
    async def test_update_user_profile_merges_legacy_string_and_writes_native_jsonb(self):
        mock_conn = _make_mock_conn_with_tx()
        mock_conn.fetchval.return_value = '{"theme":"light"}'
        mock_pool = _make_mock_pool(mock_conn)

        with patch("app.services.auth.profile.db_connection.DB_POOL", mock_pool):
            changed = await update_user_profile(uuid4(), settings={"font": "serif"})

        assert changed == ["settings_json"]
        execute_args = mock_conn.execute.await_args.args
        assert any(
            isinstance(arg, dict)
            and arg["theme"] == "light"
            and arg["font"] == "serif"
            for arg in execute_args
        )

    @pytest.mark.anyio
    async def test_get_user_profile_accepts_native_settings_json(self):
        user_id = uuid4()
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {
            "id": user_id,
            "display_name": "Claread",
            "avatar_url": None,
            "cumulative_article_count": 3,
            "settings_json": {"theme": "light"},
        }
        mock_pool = _make_mock_pool(mock_conn)

        with patch("app.services.auth.profile.db_connection.DB_POOL", mock_pool):
            profile = await get_user_profile(user_id)

        assert profile is not None
        assert profile["settings"] == {"theme": "light"}

    @pytest.mark.anyio
    async def test_pipeline_tracker_add_error_writes_native_json_array(self):
        mock_conn = AsyncMock()
        mock_pool = _make_mock_pool(mock_conn)
        tracker = PipelineRunTracker("run-1")
        tracker._pool = mock_pool

        await tracker.add_error("parse", "boom")

        execute_args = mock_conn.execute.await_args.args
        assert any(
            isinstance(arg, list)
            and arg[0]["stage"] == "parse"
            and arg[0]["message"] == "boom"
            for arg in execute_args
        )
