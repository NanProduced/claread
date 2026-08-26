"""Transactional Representation Event Coverage tests.

Verifies that G1/G2/G3 write paths publish reader_events in the same
PostgreSQL transaction as the business fact write, that true no-ops
do not advance the sequence, and that the payload validator enforces
the representation event contract.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

import anyio
import asyncpg
import pytest

from app.contracts.annotation import (
    compute_text_range_hash,
    slice_by_utf16_offsets,
    utf16_code_unit_length,
)
from app.database import connection as db_connection
from app.schemas.reader_ask import (
    ReaderAskReadingRecordAnchor,
    ReaderAskSupplementCandidate,
)
from app.schemas.reader_notes import ReaderNoteCreateRequest, ReaderNoteUpdateRequest
from app.schemas.user_annotations import (
    UserAnnotationCreateRequest,
    UserAnnotationUpdateRequest,
)
from app.schemas.user_editorial_assets import UserEditorialAssetAnchor
from app.services.reader_orchestration.supplements import (
    create_supplement,
    delete_supplement,
)
from app.services.reader_notes import (
    create_reader_note,
    delete_reader_note,
    update_reader_note,
)
from app.services.reader_orchestration.display_title_worker import (
    DisplayTitleExecutionResult,
    DisplayTitleWorkerService,
)
from app.services.reader_orchestration.event_runtime import ReaderEventRuntime
from app.services.reader_orchestration.job_bootstrap import (
    DisplayTitleJobBootstrapService,
)
from app.services.reader_orchestration.representation_event_payload import (
    MAX_KEY_LENGTH,
    MAX_TARGET_KEYS,
    REPRESENTATION_PAYLOAD_SCHEMA_VERSION,
    RepresentationPayloadError,
    build_representation_payload,
    validate_representation_payload,
)
from app.services.user_annotations import (
    create_user_annotation,
    delete_user_annotation,
    update_user_annotation,
)
from tests.reader_orchestration_test_support import (
    BASELINE_SQL,
    connect_admin,
    insert_user,
    make_pool,
    submit_article_ready,
)

pytestmark = pytest.mark.anyio

_PLAIN_TEXT = "Hello 🧠 world. Another sentence here."


class _StaticTitleGenerator:
    def __init__(self, title_zh: str = "测试标题") -> None:
        self._title_zh = title_zh

    async def generate(self, context):
        return DisplayTitleExecutionResult(
            title_zh=self._title_zh,
            usage_data={
                "aggregate": {
                    "input_tokens": 10,
                    "output_tokens": 4,
                    "total_tokens": 14,
                }
            },
            prompt_version="test",
            model_profile="fake",
            model_provider="fake",
            model_name="fake",
        )


class _FailingTitleGenerator:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def generate(self, context):
        raise self._error


@pytest.fixture
async def rep_event_env() -> asyncpg.Pool:
    """Create a fresh schema + pool and swap db_connection.DB_POOL."""
    schema_name = f"test_rep_event_{uuid4().hex}"
    admin_conn = await connect_admin()
    original_pool = db_connection.DB_POOL
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
        pool = await make_pool(schema_name)
        db_connection.DB_POOL = pool
        try:
            yield pool
        finally:
            await pool.close()
    finally:
        db_connection.DB_POOL = original_pool
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


# ---------------------------------------------------------------------------#
# Helpers
# ---------------------------------------------------------------------------#


async def _fetch_first_anchor_segment(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
    base_id: UUID,
) -> tuple[str, str, int, int, str]:
    async with pool.acquire() as conn:
        seg_row = await conn.fetchrow(
            """
            SELECT unit_id, anchor_segment_id,
                   unit_start_utf16, unit_end_utf16,
                   text_hash
            FROM anchor_segments
            WHERE reading_record_id = $1 AND base_id = $2
            ORDER BY order_index ASC
            LIMIT 1
            """,
            record_id,
            base_id,
        )
        base_text = await conn.fetchval(
            "SELECT text FROM reading_bases WHERE id = $1",
            base_id,
        )
    assert seg_row is not None
    assert isinstance(base_text, str) and base_text
    return (
        str(seg_row["unit_id"]),
        str(seg_row["anchor_segment_id"]),
        int(seg_row["unit_start_utf16"]),
        int(seg_row["unit_end_utf16"]),
        base_text,
    )


async def _count_reader_events(pool: asyncpg.Pool, record_id: UUID) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM reader_events WHERE reading_record_id = $1",
            record_id,
        )


async def _get_last_event_sequence(pool: asyncpg.Pool, record_id: UUID) -> int:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT COALESCE(MAX(sequence), 0) AS max_seq,
                   COALESCE((
                       SELECT next_sequence - 1
                       FROM reader_event_sequences
                       WHERE reading_record_id = $1
                   ), 0) AS counter_seq
            FROM reader_events
            WHERE reading_record_id = $1
            """,
            record_id,
        )
    assert row is not None
    return int(row["counter_seq"])


async def _get_latest_event(
    pool: asyncpg.Pool, record_id: UUID
) -> asyncpg.Record | None:
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            SELECT id, sequence, event_type, payload_json,
                   source_run_id, source_job_id
            FROM reader_events
            WHERE reading_record_id = $1
            ORDER BY sequence DESC
            LIMIT 1
            """,
            record_id,
        )


async def _setup_record(
    pool: asyncpg.Pool,
) -> tuple[UUID, UUID, UUID, str, str, int, int, str, str, str]:
    """Submit a plain-text article and return anchor geometry."""
    user_id = await insert_user(pool)
    result = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=_PLAIN_TEXT,
        title="Representation Event Test",
    )
    unit_id, anchor_segment_id, seg_start, seg_end, base_text = (
        await _fetch_first_anchor_segment(
            pool, record_id=result.record_id, base_id=result.base_id
        )
    )
    # Pick a sub-range strictly inside the segment.
    target_start = seg_start
    target_end = seg_start + utf16_code_unit_length("Hello")
    if target_end >= seg_end:
        target_start = seg_start
        target_end = seg_end
    selected_text = slice_by_utf16_offsets(base_text, target_start, target_end)
    assert selected_text is not None
    text_hash = compute_text_range_hash(selected_text)
    return (
        user_id,
        result.record_id,
        result.base_id,
        unit_id,
        anchor_segment_id,
        target_start,
        target_end,
        selected_text,
        text_hash,
        base_text,
    )


def _build_anchor(
    *,
    record_id: UUID,
    base_id: UUID,
    unit_id: str,
    anchor_segment_id: str,
    start_offset: int,
    end_offset: int,
    selected_text: str,
    text_hash: str,
) -> UserEditorialAssetAnchor:
    return UserEditorialAssetAnchor(
        record_id=str(record_id),
        base_id=str(base_id),
        generation=1,
        unit_id=unit_id,
        anchor_segment_id=anchor_segment_id,
        start_offset=start_offset,
        end_offset=end_offset,
        selected_text=selected_text,
        text_hash=text_hash,
    )


# ---------------------------------------------------------------------------#
# Section 1: Validator / Builder unit tests (no DB)
# ---------------------------------------------------------------------------#


class TestRepresentationPayloadValidator:
    """Pure unit tests for build_representation_payload / validate_representation_payload."""

    def test_build_valid_user_assets_upsert(self) -> None:
        payload = build_representation_payload(
            representation_section="user_assets",
            operation="upsert",
            generation=1,
            base_id=str(uuid4()),
            target_keys=[str(uuid4())],
        )
        assert payload["schema_version"] == REPRESENTATION_PAYLOAD_SCHEMA_VERSION
        assert payload["representation_section"] == "user_assets"
        assert payload["operation"] == "upsert"

    def test_build_valid_ask_supplements_delete(self) -> None:
        payload = build_representation_payload(
            representation_section="ask_supplements",
            operation="delete",
            generation=3,
            base_id=str(uuid4()),
            target_keys=[str(uuid4())],
        )
        assert payload["representation_section"] == "ask_supplements"

    def test_build_valid_record_metadata_status_changed(self) -> None:
        payload = build_representation_payload(
            representation_section="record_metadata",
            operation="status_changed",
            generation=1,
            base_id=str(uuid4()),
            target_keys=["title_generation_status"],
        )
        assert payload["representation_section"] == "record_metadata"

    def test_validate_rejects_forbidden_key_note_text(self) -> None:
        payload = {
            "schema_version": 1,
            "representation_section": "user_assets",
            "operation": "upsert",
            "generation": 1,
            "base_id": str(uuid4()),
            "target_keys": ["a"],
            "note_text": "leaked content",
        }
        with pytest.raises(RepresentationPayloadError, match="forbidden key"):
            validate_representation_payload(payload)

    def test_validate_rejects_forbidden_key_selected_text(self) -> None:
        payload = {
            "schema_version": 1,
            "representation_section": "user_assets",
            "operation": "upsert",
            "generation": 1,
            "base_id": str(uuid4()),
            "target_keys": ["a"],
            "selected_text": "leaked",
        }
        with pytest.raises(RepresentationPayloadError, match="forbidden key"):
            validate_representation_payload(payload)

    def test_validate_rejects_forbidden_key_prompt(self) -> None:
        payload = {
            "schema_version": 1,
            "representation_section": "ask_supplements",
            "operation": "upsert",
            "generation": 1,
            "base_id": str(uuid4()),
            "target_keys": ["a"],
            "prompt": "leaked prompt",
        }
        with pytest.raises(RepresentationPayloadError, match="forbidden key"):
            validate_representation_payload(payload)

    def test_validate_rejects_forbidden_key_reload_policy(self) -> None:
        payload = {
            "schema_version": 1,
            "representation_section": "user_assets",
            "operation": "upsert",
            "generation": 1,
            "base_id": str(uuid4()),
            "target_keys": ["a"],
            "reload_policy": "force",
        }
        with pytest.raises(RepresentationPayloadError, match="forbidden key"):
            validate_representation_payload(payload)

    def test_validate_rejects_forbidden_key_cursor_only(self) -> None:
        payload = {
            "schema_version": 1,
            "representation_section": "user_assets",
            "operation": "upsert",
            "generation": 1,
            "base_id": str(uuid4()),
            "target_keys": ["a"],
            "cursor_only": True,
        }
        with pytest.raises(RepresentationPayloadError, match="forbidden key"):
            validate_representation_payload(payload)

    def test_validate_rejects_unknown_section(self) -> None:
        with pytest.raises(RepresentationPayloadError, match="unknown representation_section"):
            build_representation_payload(
                representation_section="unknown_section",
                operation="upsert",
                generation=1,
                base_id=str(uuid4()),
                target_keys=["a"],
            )

    def test_validate_rejects_unknown_operation_for_section(self) -> None:
        with pytest.raises(RepresentationPayloadError, match="not allowed for section"):
            build_representation_payload(
                representation_section="user_assets",
                operation="status_changed",
                generation=1,
                base_id=str(uuid4()),
                target_keys=["a"],
            )

    def test_validate_rejects_empty_target_keys(self) -> None:
        with pytest.raises(RepresentationPayloadError, match="target_keys must not be empty"):
            build_representation_payload(
                representation_section="user_assets",
                operation="upsert",
                generation=1,
                base_id=str(uuid4()),
                target_keys=[],
            )

    def test_validate_rejects_too_many_target_keys(self) -> None:
        with pytest.raises(RepresentationPayloadError, match="exceeds limit"):
            build_representation_payload(
                representation_section="user_assets",
                operation="upsert",
                generation=1,
                base_id=str(uuid4()),
                target_keys=[str(i) for i in range(MAX_TARGET_KEYS + 1)],
            )

    def test_validate_rejects_target_key_too_long(self) -> None:
        with pytest.raises(RepresentationPayloadError, match="exceeds limit"):
            build_representation_payload(
                representation_section="user_assets",
                operation="upsert",
                generation=1,
                base_id=str(uuid4()),
                target_keys=["x" * (MAX_KEY_LENGTH + 1)],
            )

    def test_validate_rejects_invalid_generation(self) -> None:
        with pytest.raises(RepresentationPayloadError, match="generation"):
            build_representation_payload(
                representation_section="user_assets",
                operation="upsert",
                generation=0,
                base_id=str(uuid4()),
                target_keys=["a"],
            )

    def test_validate_rejects_empty_base_id(self) -> None:
        with pytest.raises(RepresentationPayloadError, match="base_id"):
            build_representation_payload(
                representation_section="user_assets",
                operation="upsert",
                generation=1,
                base_id="",
                target_keys=["a"],
            )

    def test_validate_rejects_extra_top_level_keys(self) -> None:
        payload = {
            "schema_version": 1,
            "representation_section": "user_assets",
            "operation": "upsert",
            "generation": 1,
            "base_id": str(uuid4()),
            "target_keys": ["a"],
            "extra_field": "bad",
        }
        with pytest.raises(RepresentationPayloadError, match="unexpected payload keys"):
            validate_representation_payload(payload)

    def test_validate_rejects_metadata_field_not_in_allowlist(self) -> None:
        with pytest.raises(RepresentationPayloadError, match="not in allowlist"):
            build_representation_payload(
                representation_section="record_metadata",
                operation="status_changed",
                generation=1,
                base_id=str(uuid4()),
                target_keys=["unknown_field"],
            )

    def test_validate_accepts_all_metadata_fields(self) -> None:
        """All four metadata fields (display_title_zh, title_generation_status,
        title_generation_error_code, title_generation_error_message) must be
        accepted by the allowlist."""
        for field in (
            "display_title_zh",
            "title_generation_status",
            "title_generation_error_code",
            "title_generation_error_message",
        ):
            payload = build_representation_payload(
                representation_section="record_metadata",
                operation="status_changed",
                generation=1,
                base_id=str(uuid4()),
                target_keys=[field],
            )
            assert payload["target_keys"] == [field]

    def test_validate_rejects_oversized_payload(self) -> None:
        """Payload exceeding MAX_PAYLOAD_BYTES must be rejected, not truncated.

        Use a legitimate target_keys count (≤64) with max-length keys and a
        long base_id to exceed the byte limit without triggering the
        target_keys count guard first.
        """
        # 64 keys × 128 chars each + long base_id > 16KB serialized.
        max_keys = ["k" * MAX_KEY_LENGTH for _ in range(MAX_TARGET_KEYS)]
        long_base_id = "b" * 8000
        with pytest.raises(RepresentationPayloadError, match="exceeds limit"):
            build_representation_payload(
                representation_section="user_assets",
                operation="upsert",
                generation=1,
                base_id=long_base_id,
                target_keys=max_keys,
            )

    def test_build_does_not_leak_content_in_payload(self) -> None:
        """The builder only accepts the canonical fields — no way to inject content."""
        payload = build_representation_payload(
            representation_section="user_assets",
            operation="upsert",
            generation=1,
            base_id=str(uuid4()),
            target_keys=[str(uuid4())],
        )
        # The payload must only contain the 6 canonical fields.
        assert set(payload.keys()) == {
            "schema_version",
            "representation_section",
            "operation",
            "generation",
            "base_id",
            "target_keys",
        }
        # No forbidden key can appear.
        for key in payload:
            assert key not in {
                "note_text", "selected_text", "content", "prompt",
                "answer", "raw_output", "reload_policy", "cursor_only",
            }


    # ------------------------------------------------------------------
    # image_overrides section allowlist contract
    # ------------------------------------------------------------------
    @pytest.mark.parametrize("operation", ["upsert", "delete"])
    def test_build_valid_image_overrides_operations(self, operation: str) -> None:
        target_key = f"{uuid4()}:b12:{'-' if operation == 'upsert' else '0'}"
        payload = build_representation_payload(
            representation_section="image_overrides",
            operation=operation,
            generation=1,
            base_id=str(uuid4()),
            target_keys=[target_key],
        )
        assert payload["representation_section"] == "image_overrides"
        assert payload["operation"] == operation
        assert payload["target_keys"] == [target_key]
    @pytest.mark.parametrize(
        "operation", ["merge", "reactivate", "status_changed", "restore"]
    )
    def test_image_overrides_rejects_operations_outside_allowlist(
        self, operation: str
    ) -> None:
        with pytest.raises(RepresentationPayloadError, match="not allowed for section"):
            build_representation_payload(
                representation_section="image_overrides",
                operation=operation,
                generation=1,
                base_id=str(uuid4()),
                target_keys=[f"{uuid4()}:b1:-"],
            )


# ---------------------------------------------------------------------------#
# Section 2: G3 — Display title representation events (DB integration)
# ---------------------------------------------------------------------------#


class TestG3DisplayTitleEvents:
    """G3: record_metadata (display-title status) representation events."""

    async def test_bootstrap_pending_publishes_record_state_changed(
        self, rep_event_env: asyncpg.Pool
    ) -> None:
        """When bootstrap transitions title_generation_status from
        failed_retryable back to pending (retry), a record_state_changed
        event is published in the same transaction.

        First-time bootstrap is a no-op for status because the DB default
        is already 'pending' — only the retry path (failed_retryable ->
        pending) publishes a pending transition event.
        """
        user_id = await insert_user(rep_event_env)
        article = await submit_article_ready(
            rep_event_env,
            user_id=user_id,
            plain_text=_PLAIN_TEXT,
            title="G3 Pending Test",
        )
        # First bootstrap: creates job, but status is already 'pending' (DB
        # default) so no event is published.
        bootstrap = DisplayTitleJobBootstrapService(pool=rep_event_env)
        await bootstrap.bootstrap_display_title_job(
            record_id=article.record_id,
            user_id=user_id,
        )
        seq_after_first = await _get_last_event_sequence(
            rep_event_env, article.record_id
        )

        # Simulate a failed title generation: set status to failed_retryable
        # and remove the existing job so bootstrap can create a new one
        # (otherwise the existing_job check short-circuits).
        async with rep_event_env.acquire() as conn:
            await conn.execute(
                "UPDATE reading_records SET title_generation_status = 'failed_retryable' "
                "WHERE id = $1",
                article.record_id,
            )
            await conn.execute(
                "DELETE FROM reader_jobs WHERE reading_record_id = $1",
                article.record_id,
            )

        # Second bootstrap: transitions failed_retryable -> pending and
        # publishes a representation event in the same transaction.
        await bootstrap.bootstrap_display_title_job(
            record_id=article.record_id,
            user_id=user_id,
        )
        seq_after_second = await _get_last_event_sequence(
            rep_event_env, article.record_id
        )
        assert seq_after_second == seq_after_first + 1

        event = await _get_latest_event(rep_event_env, article.record_id)
        assert event is not None
        assert event["event_type"] == "record_state_changed"
        payload = event["payload_json"]
        assert payload["representation_section"] == "record_metadata"
        assert payload["operation"] == "status_changed"
        assert payload["target_keys"] == [
            "title_generation_status",
            "title_generation_error_code",
            "title_generation_error_message",
        ]

    async def test_already_pending_bootstrap_is_noop_no_event(
        self, rep_event_env: asyncpg.Pool
    ) -> None:
        """If title_generation_status is already 'pending', bootstrap must
        NOT publish a new event or advance the sequence."""
        user_id = await insert_user(rep_event_env)
        article = await submit_article_ready(
            rep_event_env,
            user_id=user_id,
            plain_text=_PLAIN_TEXT,
            title="G3 Noop Test",
        )
        # First bootstrap: transitions to pending + publishes event.
        bootstrap = DisplayTitleJobBootstrapService(pool=rep_event_env)
        await bootstrap.bootstrap_display_title_job(
            record_id=article.record_id,
            user_id=user_id,
        )
        seq_after_first = await _get_last_event_sequence(
            rep_event_env, article.record_id
        )

        # Second bootstrap: already pending, should be no-op.
        # The existing_job check returns [] before hitting the UPDATE.
        await bootstrap.bootstrap_display_title_job(
            record_id=article.record_id,
            user_id=user_id,
        )
        seq_after_second = await _get_last_event_sequence(
            rep_event_env, article.record_id
        )
        assert seq_after_second == seq_after_first

    async def test_title_success_publishes_representation_event(
        self, rep_event_env: asyncpg.Pool
    ) -> None:
        """When the display-title worker succeeds, a record_state_changed
        event is published with target_keys including display_title_zh."""
        user_id = await insert_user(rep_event_env)
        article = await submit_article_ready(
            rep_event_env,
            user_id=user_id,
            plain_text=_PLAIN_TEXT,
            title="G3 Success Test",
        )
        bootstrap = DisplayTitleJobBootstrapService(pool=rep_event_env)
        await bootstrap.bootstrap_display_title_job(
            record_id=article.record_id,
            user_id=user_id,
        )
        seq_before = await _get_last_event_sequence(rep_event_env, article.record_id)

        worker = DisplayTitleWorkerService(
            pool=rep_event_env,
            generator=_StaticTitleGenerator(title_zh="测试成功标题"),
        )
        result = await worker.process_next_display_title_job(
            lease_owner="test-worker",
            lease_duration=timedelta(seconds=30),
        )
        assert result is not None
        assert result.status == "succeeded"

        seq_after = await _get_last_event_sequence(rep_event_env, article.record_id)
        assert seq_after == seq_before + 1

        event = await _get_latest_event(rep_event_env, article.record_id)
        assert event is not None
        assert event["event_type"] == "record_state_changed"
        payload = event["payload_json"]
        assert payload["representation_section"] == "record_metadata"
        assert "display_title_zh" in payload["target_keys"]
        assert "title_generation_status" in payload["target_keys"]

    async def test_title_failed_retryable_publishes_representation_event(
        self, rep_event_env: asyncpg.Pool
    ) -> None:
        """When the display-title worker enters failed_retryable, a
        record_state_changed event is published."""
        from app.services.reader_orchestration.display_title_worker import (
            DisplayTitleGenerationError,
        )

        user_id = await insert_user(rep_event_env)
        article = await submit_article_ready(
            rep_event_env,
            user_id=user_id,
            plain_text=_PLAIN_TEXT,
            title="G3 Fail Test",
        )
        bootstrap = DisplayTitleJobBootstrapService(pool=rep_event_env)
        await bootstrap.bootstrap_display_title_job(
            record_id=article.record_id,
            user_id=user_id,
        )
        seq_before = await _get_last_event_sequence(rep_event_env, article.record_id)

        error = DisplayTitleGenerationError(
            "test failure",
            failure_class="provider",
            failure_code="test_error",
        )
        worker = DisplayTitleWorkerService(
            pool=rep_event_env,
            generator=_FailingTitleGenerator(error),
        )
        result = await worker.process_next_display_title_job(
            lease_owner="test-worker",
            lease_duration=timedelta(seconds=30),
            retry_delay=timedelta(minutes=10),
        )
        assert result is not None
        # Job result status is "retry_later"; the DB title_generation_status
        # transitions to "failed_retryable".
        assert result.status == "retry_later"

        async with rep_event_env.acquire() as conn:
            record_row = await conn.fetchrow(
                "SELECT title_generation_status FROM reading_records WHERE id = $1",
                article.record_id,
            )
        assert record_row["title_generation_status"] == "failed_retryable"

        seq_after = await _get_last_event_sequence(rep_event_env, article.record_id)
        assert seq_after == seq_before + 1

        event = await _get_latest_event(rep_event_env, article.record_id)
        assert event is not None
        assert event["event_type"] == "record_state_changed"
        payload = event["payload_json"]
        assert payload["representation_section"] == "record_metadata"
        assert "title_generation_status" in payload["target_keys"]


# ---------------------------------------------------------------------------#
# Section 3: G1 — User Editorial Assets events (DB integration)
# ---------------------------------------------------------------------------#


class TestG1UserAssetsEvents:
    """G1: user_annotations + reader_notes publish projection_ops events."""

    async def test_create_highlight_publishes_projection_ops(
        self, rep_event_env: asyncpg.Pool
    ) -> None:
        (
            user_id,
            record_id,
            base_id,
            unit_id,
            anchor_segment_id,
            start_offset,
            end_offset,
            selected_text,
            text_hash,
            _,
        ) = await _setup_record(rep_event_env)
        seq_before = await _get_last_event_sequence(rep_event_env, record_id)

        anchor = _build_anchor(
            record_id=record_id,
            base_id=base_id,
            unit_id=unit_id,
            anchor_segment_id=anchor_segment_id,
            start_offset=start_offset,
            end_offset=end_offset,
            selected_text=selected_text,
            text_hash=text_hash,
        )
        req = UserAnnotationCreateRequest(
            anchor=anchor,
            selected_text=selected_text,
            color="warm_yellow",
        )
        response = await create_user_annotation(user_id, req)

        seq_after = await _get_last_event_sequence(rep_event_env, record_id)
        assert seq_after == seq_before + 1

        event = await _get_latest_event(rep_event_env, record_id)
        assert event is not None
        assert event["event_type"] == "projection_ops"
        payload = event["payload_json"]
        assert payload["representation_section"] == "user_assets"
        assert payload["operation"] == "upsert"
        assert str(response.id) in payload["target_keys"]
        # Payload must not leak selected_text.
        assert "selected_text" not in payload
        assert "content" not in payload

    async def test_update_highlight_color_publishes_event(
        self, rep_event_env: asyncpg.Pool
    ) -> None:
        (
            user_id,
            record_id,
            base_id,
            unit_id,
            anchor_segment_id,
            start_offset,
            end_offset,
            selected_text,
            text_hash,
            _,
        ) = await _setup_record(rep_event_env)
        anchor = _build_anchor(
            record_id=record_id,
            base_id=base_id,
            unit_id=unit_id,
            anchor_segment_id=anchor_segment_id,
            start_offset=start_offset,
            end_offset=end_offset,
            selected_text=selected_text,
            text_hash=text_hash,
        )
        create_req = UserAnnotationCreateRequest(
            anchor=anchor, selected_text=selected_text, color="warm_yellow"
        )
        created = await create_user_annotation(user_id, create_req)
        seq_before = await _get_last_event_sequence(rep_event_env, record_id)

        update_req = UserAnnotationUpdateRequest(color="soft_mint")
        await update_user_annotation(user_id, created.id, update_req)

        seq_after = await _get_last_event_sequence(rep_event_env, record_id)
        assert seq_after == seq_before + 1

        event = await _get_latest_event(rep_event_env, record_id)
        assert event is not None
        assert event["event_type"] == "projection_ops"
        payload = event["payload_json"]
        assert payload["representation_section"] == "user_assets"
        assert payload["operation"] == "upsert"

    async def test_update_highlight_same_color_is_noop_no_event(
        self, rep_event_env: asyncpg.Pool
    ) -> None:
        """Updating with the same color must NOT publish an event or advance sequence."""
        (
            user_id,
            record_id,
            base_id,
            unit_id,
            anchor_segment_id,
            start_offset,
            end_offset,
            selected_text,
            text_hash,
            _,
        ) = await _setup_record(rep_event_env)
        anchor = _build_anchor(
            record_id=record_id,
            base_id=base_id,
            unit_id=unit_id,
            anchor_segment_id=anchor_segment_id,
            start_offset=start_offset,
            end_offset=end_offset,
            selected_text=selected_text,
            text_hash=text_hash,
        )
        create_req = UserAnnotationCreateRequest(
            anchor=anchor, selected_text=selected_text, color="warm_yellow"
        )
        created = await create_user_annotation(user_id, create_req)
        seq_before = await _get_last_event_sequence(rep_event_env, record_id)

        # Same color — no-op.
        update_req = UserAnnotationUpdateRequest(color="warm_yellow")
        await update_user_annotation(user_id, created.id, update_req)

        seq_after = await _get_last_event_sequence(rep_event_env, record_id)
        assert seq_after == seq_before

    async def test_delete_highlight_publishes_delete_event(
        self, rep_event_env: asyncpg.Pool
    ) -> None:
        (
            user_id,
            record_id,
            base_id,
            unit_id,
            anchor_segment_id,
            start_offset,
            end_offset,
            selected_text,
            text_hash,
            _,
        ) = await _setup_record(rep_event_env)
        anchor = _build_anchor(
            record_id=record_id,
            base_id=base_id,
            unit_id=unit_id,
            anchor_segment_id=anchor_segment_id,
            start_offset=start_offset,
            end_offset=end_offset,
            selected_text=selected_text,
            text_hash=text_hash,
        )
        create_req = UserAnnotationCreateRequest(
            anchor=anchor, selected_text=selected_text, color="warm_yellow"
        )
        created = await create_user_annotation(user_id, create_req)
        seq_before = await _get_last_event_sequence(rep_event_env, record_id)

        await delete_user_annotation(user_id, created.id)

        seq_after = await _get_last_event_sequence(rep_event_env, record_id)
        assert seq_after == seq_before + 1

        event = await _get_latest_event(rep_event_env, record_id)
        assert event is not None
        assert event["event_type"] == "projection_ops"
        payload = event["payload_json"]
        assert payload["representation_section"] == "user_assets"
        assert payload["operation"] == "delete"

    async def test_create_note_publishes_projection_ops(
        self, rep_event_env: asyncpg.Pool
    ) -> None:
        (
            user_id,
            record_id,
            base_id,
            unit_id,
            anchor_segment_id,
            start_offset,
            end_offset,
            selected_text,
            text_hash,
            _,
        ) = await _setup_record(rep_event_env)
        seq_before = await _get_last_event_sequence(rep_event_env, record_id)

        anchor = _build_anchor(
            record_id=record_id,
            base_id=base_id,
            unit_id=unit_id,
            anchor_segment_id=anchor_segment_id,
            start_offset=start_offset,
            end_offset=end_offset,
            selected_text=selected_text,
            text_hash=text_hash,
        )
        req = ReaderNoteCreateRequest(
            anchor=anchor,
            selected_text=selected_text,
            note_text="This is a test note.",
            quote_mode="text_range",
        )
        response = await create_reader_note(user_id, req)

        seq_after = await _get_last_event_sequence(rep_event_env, record_id)
        assert seq_after == seq_before + 1

        event = await _get_latest_event(rep_event_env, record_id)
        assert event is not None
        assert event["event_type"] == "projection_ops"
        payload = event["payload_json"]
        assert payload["representation_section"] == "user_assets"
        assert payload["operation"] == "upsert"
        assert str(response.id) in payload["target_keys"]
        # Payload must not leak note_text.
        assert "note_text" not in payload
        assert "content" not in payload

    async def test_update_note_text_publishes_event(
        self, rep_event_env: asyncpg.Pool
    ) -> None:
        (
            user_id,
            record_id,
            base_id,
            unit_id,
            anchor_segment_id,
            start_offset,
            end_offset,
            selected_text,
            text_hash,
            _,
        ) = await _setup_record(rep_event_env)
        anchor = _build_anchor(
            record_id=record_id,
            base_id=base_id,
            unit_id=unit_id,
            anchor_segment_id=anchor_segment_id,
            start_offset=start_offset,
            end_offset=end_offset,
            selected_text=selected_text,
            text_hash=text_hash,
        )
        create_req = ReaderNoteCreateRequest(
            anchor=anchor,
            selected_text=selected_text,
            note_text="Original note.",
            quote_mode="text_range",
        )
        created = await create_reader_note(user_id, create_req)
        seq_before = await _get_last_event_sequence(rep_event_env, record_id)

        update_req = ReaderNoteUpdateRequest(note_text="Updated note text.")
        await update_reader_note(user_id, created.id, update_req)

        seq_after = await _get_last_event_sequence(rep_event_env, record_id)
        assert seq_after == seq_before + 1

    async def test_update_note_same_text_is_noop_no_event(
        self, rep_event_env: asyncpg.Pool
    ) -> None:
        """Updating with the same note_text must NOT publish an event."""
        (
            user_id,
            record_id,
            base_id,
            unit_id,
            anchor_segment_id,
            start_offset,
            end_offset,
            selected_text,
            text_hash,
            _,
        ) = await _setup_record(rep_event_env)
        anchor = _build_anchor(
            record_id=record_id,
            base_id=base_id,
            unit_id=unit_id,
            anchor_segment_id=anchor_segment_id,
            start_offset=start_offset,
            end_offset=end_offset,
            selected_text=selected_text,
            text_hash=text_hash,
        )
        create_req = ReaderNoteCreateRequest(
            anchor=anchor,
            selected_text=selected_text,
            note_text="Same note.",
            quote_mode="text_range",
        )
        created = await create_reader_note(user_id, create_req)
        seq_before = await _get_last_event_sequence(rep_event_env, record_id)

        update_req = ReaderNoteUpdateRequest(note_text="Same note.")
        await update_reader_note(user_id, created.id, update_req)

        seq_after = await _get_last_event_sequence(rep_event_env, record_id)
        assert seq_after == seq_before

    async def test_delete_note_publishes_delete_event(
        self, rep_event_env: asyncpg.Pool
    ) -> None:
        (
            user_id,
            record_id,
            base_id,
            unit_id,
            anchor_segment_id,
            start_offset,
            end_offset,
            selected_text,
            text_hash,
            _,
        ) = await _setup_record(rep_event_env)
        anchor = _build_anchor(
            record_id=record_id,
            base_id=base_id,
            unit_id=unit_id,
            anchor_segment_id=anchor_segment_id,
            start_offset=start_offset,
            end_offset=end_offset,
            selected_text=selected_text,
            text_hash=text_hash,
        )
        create_req = ReaderNoteCreateRequest(
            anchor=anchor,
            selected_text=selected_text,
            note_text="To be deleted.",
            quote_mode="text_range",
        )
        created = await create_reader_note(user_id, create_req)
        seq_before = await _get_last_event_sequence(rep_event_env, record_id)

        await delete_reader_note(user_id, created.id)

        seq_after = await _get_last_event_sequence(rep_event_env, record_id)
        assert seq_after == seq_before + 1

        event = await _get_latest_event(rep_event_env, record_id)
        assert event is not None
        assert event["event_type"] == "projection_ops"
        payload = event["payload_json"]
        assert payload["representation_section"] == "user_assets"
        assert payload["operation"] == "delete"


# ---------------------------------------------------------------------------#
# Section 4: G2 — Ask Supplements events (DB integration)
# ---------------------------------------------------------------------------#


class TestG2AskSupplementEvents:
    """G2: reader_ask_supplements publish projection_ops events."""

    async def _make_candidate(
        self,
        *,
        record_id: UUID,
        base_id: UUID,
        unit_id: str,
        anchor_segment_id: str,
        start_offset: int,
        end_offset: int,
        selected_text: str,
        text_hash: str,
    ) -> ReaderAskSupplementCandidate:
        anchor = ReaderAskReadingRecordAnchor(
            record_id=str(record_id),
            base_id=str(base_id),
            generation=1,
            unit_id=unit_id,
            anchor_segment_id=anchor_segment_id,
            start_offset=start_offset,
            end_offset=end_offset,
            selected_text=selected_text,
            text_hash=text_hash,
        )
        return ReaderAskSupplementCandidate(
            candidate_id=str(uuid4()),
            supplement_type="grammar_note",
            target_key=f"reading-record:{record_id}:segment:{anchor_segment_id}:{start_offset}:{end_offset}",
            sentence_id=anchor_segment_id,
            title="AI 补充语法旁注",
            content=(
                "This is a sufficiently long supplement content that "
                "passes the minimum length check of sixty characters."
            ),
            anchor=anchor,
            schema_version="reader-ask-supplement-v1",
            created_from_turn_run_id=str(uuid4()),
        )

    async def test_create_supplement_publishes_projection_ops(
        self, rep_event_env: asyncpg.Pool
    ) -> None:
        (
            user_id,
            record_id,
            base_id,
            unit_id,
            anchor_segment_id,
            start_offset,
            end_offset,
            selected_text,
            text_hash,
            _,
        ) = await _setup_record(rep_event_env)
        seq_before = await _get_last_event_sequence(rep_event_env, record_id)

        candidate = await self._make_candidate(
            record_id=record_id,
            base_id=base_id,
            unit_id=unit_id,
            anchor_segment_id=anchor_segment_id,
            start_offset=start_offset,
            end_offset=end_offset,
            selected_text=selected_text,
            text_hash=text_hash,
        )
        row = await create_supplement(
            user_id=user_id,
            reading_record_id=record_id,
            candidate=candidate,
        )

        seq_after = await _get_last_event_sequence(rep_event_env, record_id)
        assert seq_after == seq_before + 1

        event = await _get_latest_event(rep_event_env, record_id)
        assert event is not None
        assert event["event_type"] == "projection_ops"
        payload = event["payload_json"]
        assert payload["representation_section"] == "ask_supplements"
        assert payload["operation"] == "upsert"
        assert str(row["id"]) in payload["target_keys"]
        # Payload must not leak content.
        assert "content" not in payload
        assert "prompt" not in payload
        assert "answer" not in payload

    async def test_create_supplement_conflict_is_noop_no_event(
        self, rep_event_env: asyncpg.Pool
    ) -> None:
        """Creating a supplement with an existing ID (ON CONFLICT DO NOTHING)
        must NOT publish an event or advance the sequence."""
        (
            user_id,
            record_id,
            base_id,
            unit_id,
            anchor_segment_id,
            start_offset,
            end_offset,
            selected_text,
            text_hash,
            _,
        ) = await _setup_record(rep_event_env)

        candidate = await self._make_candidate(
            record_id=record_id,
            base_id=base_id,
            unit_id=unit_id,
            anchor_segment_id=anchor_segment_id,
            start_offset=start_offset,
            end_offset=end_offset,
            selected_text=selected_text,
            text_hash=text_hash,
        )
        await create_supplement(
            user_id=user_id,
            reading_record_id=record_id,
            candidate=candidate,
        )
        seq_before = await _get_last_event_sequence(rep_event_env, record_id)

        # Same candidate_id → ON CONFLICT DO NOTHING → no-op.
        await create_supplement(
            user_id=user_id,
            reading_record_id=record_id,
            candidate=candidate,
        )
        seq_after = await _get_last_event_sequence(rep_event_env, record_id)
        assert seq_after == seq_before

    async def test_delete_supplement_publishes_delete_event(
        self, rep_event_env: asyncpg.Pool
    ) -> None:
        (
            user_id,
            record_id,
            base_id,
            unit_id,
            anchor_segment_id,
            start_offset,
            end_offset,
            selected_text,
            text_hash,
            _,
        ) = await _setup_record(rep_event_env)

        candidate = await self._make_candidate(
            record_id=record_id,
            base_id=base_id,
            unit_id=unit_id,
            anchor_segment_id=anchor_segment_id,
            start_offset=start_offset,
            end_offset=end_offset,
            selected_text=selected_text,
            text_hash=text_hash,
        )
        row = await create_supplement(
            user_id=user_id,
            reading_record_id=record_id,
            candidate=candidate,
        )
        seq_before = await _get_last_event_sequence(rep_event_env, record_id)

        await delete_supplement(user_id, row["id"])

        seq_after = await _get_last_event_sequence(rep_event_env, record_id)
        assert seq_after == seq_before + 1

        event = await _get_latest_event(rep_event_env, record_id)
        assert event is not None
        assert event["event_type"] == "projection_ops"
        payload = event["payload_json"]
        assert payload["representation_section"] == "ask_supplements"
        assert payload["operation"] == "delete"

    async def test_delete_supplement_already_deleted_is_noop_no_event(
        self, rep_event_env: asyncpg.Pool
    ) -> None:
        """Deleting an already-deleted supplement must NOT publish an event."""
        (
            user_id,
            record_id,
            base_id,
            unit_id,
            anchor_segment_id,
            start_offset,
            end_offset,
            selected_text,
            text_hash,
            _,
        ) = await _setup_record(rep_event_env)

        candidate = await self._make_candidate(
            record_id=record_id,
            base_id=base_id,
            unit_id=unit_id,
            anchor_segment_id=anchor_segment_id,
            start_offset=start_offset,
            end_offset=end_offset,
            selected_text=selected_text,
            text_hash=text_hash,
        )
        row = await create_supplement(
            user_id=user_id,
            reading_record_id=record_id,
            candidate=candidate,
        )
        await delete_supplement(user_id, row["id"])
        seq_before = await _get_last_event_sequence(rep_event_env, record_id)

        # Second delete — already deleted, no-op.
        result = await delete_supplement(user_id, row["id"])
        assert result is None

        seq_after = await _get_last_event_sequence(rep_event_env, record_id)
        assert seq_after == seq_before


# ---------------------------------------------------------------------------#
# Section 5: Transaction rollback test
# ---------------------------------------------------------------------------#


class TestTransactionRollback:
    """If the event insert fails, the business write must roll back."""

    async def test_event_failure_rolls_back_highlight_create(
        self, rep_event_env: asyncpg.Pool, monkeypatch
    ) -> None:
        """If publish_event_in_transaction raises, the annotation INSERT
        must be rolled back — no orphaned annotation row."""
        (
            user_id,
            record_id,
            base_id,
            unit_id,
            anchor_segment_id,
            start_offset,
            end_offset,
            selected_text,
            text_hash,
            _,
        ) = await _setup_record(rep_event_env)

        anchor = _build_anchor(
            record_id=record_id,
            base_id=base_id,
            unit_id=unit_id,
            anchor_segment_id=anchor_segment_id,
            start_offset=start_offset,
            end_offset=end_offset,
            selected_text=selected_text,
            text_hash=text_hash,
        )
        req = UserAnnotationCreateRequest(
            anchor=anchor, selected_text=selected_text, color="warm_yellow"
        )

        call_count = 0

        async def _failing_publish(self, conn, **kwargs):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("simulated event insert failure")

        monkeypatch.setattr(
            "app.services.user_annotations.ReaderEventRuntime.publish_event_in_transaction",
            _failing_publish,
        )

        with pytest.raises(RuntimeError, match="simulated event insert failure"):
            await create_user_annotation(user_id, req)

        # The annotation must NOT exist (rolled back).
        async with rep_event_env.acquire() as conn:
            count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM user_annotations
                WHERE user_id = $1 AND reading_record_id = $2
                """,
                user_id,
                record_id,
            )
        assert count == 0
        assert call_count == 1


# ---------------------------------------------------------------------------#
# Section 6: — Stale base/generation fence (no event on stale assets)
# ---------------------------------------------------------------------------#


class TestStaleFenceNoEvent:
    """Assets on a previous base/generation must NOT publish representation
    events when modified — they don't affect the current snapshot."""

    async def test_active_fence_blocks_concurrent_rebase_until_transaction_commits(
        self, rep_event_env: asyncpg.Pool
    ) -> None:
        """A matching fence must lock the record through event publication.

        Otherwise a rebase can commit between the fence check and the event
        insert, leaving a stale representation event in the cursor stream.
        """
        (
            _user_id,
            record_id,
            base_id,
            *_rest,
        ) = await _setup_record(rep_event_env)
        runtime = ReaderEventRuntime()
        rebase_started = anyio.Event()
        rebase_finished = anyio.Event()

        async with rep_event_env.acquire() as fence_conn:
            async with rep_event_env.acquire() as rebase_conn:

                async def rebase_record() -> None:
                    rebase_started.set()
                    await rebase_conn.execute(
                        "UPDATE reading_records SET deleted_at = NOW() WHERE id = $1",
                        record_id,
                    )
                    rebase_finished.set()

                async with anyio.create_task_group() as task_group:
                    async with fence_conn.transaction():
                        assert await runtime.is_active_fence(
                            fence_conn,
                            record_id=record_id,
                            base_id=base_id,
                            generation=1,
                        )
                        task_group.start_soon(rebase_record)
                        await rebase_started.wait()
                        with anyio.move_on_after(0.15) as timeout_scope:
                            await rebase_finished.wait()
                        assert timeout_scope.cancel_called

                    await rebase_finished.wait()
    async def test_update_annotation_on_stale_base_no_event(
        self, rep_event_env: asyncpg.Pool
    ) -> None:
        """Updating a highlight whose base_id/generation no longer matches
        the record's active_base_id/generation must NOT publish an event
        or advance the sequence."""
        (
            user_id,
            record_id,
            base_id,
            unit_id,
            anchor_segment_id,
            start_offset,
            end_offset,
            selected_text,
            text_hash,
            _,
        ) = await _setup_record(rep_event_env)

        anchor = _build_anchor(
            record_id=record_id,
            base_id=base_id,
            unit_id=unit_id,
            anchor_segment_id=anchor_segment_id,
            start_offset=start_offset,
            end_offset=end_offset,
            selected_text=selected_text,
            text_hash=text_hash,
        )
        req = UserAnnotationCreateRequest(
            anchor=anchor, selected_text=selected_text, color="warm_yellow"
        )
        response = await create_user_annotation(user_id, req)
        seq_after_create = await _get_last_event_sequence(
            rep_event_env, record_id
        )

        # Simulate a stale-base asset: bump the annotation's generation so
        # it no longer matches the record's active generation.
        async with rep_event_env.acquire() as conn:
            await conn.execute(
                "UPDATE user_annotations SET generation = 999 WHERE id = $1",
                response.id,
            )

        # Update the annotation's color — the write succeeds but no event.
        update_req = UserAnnotationUpdateRequest(color="soft_mint")
        await update_user_annotation(user_id, response.id, update_req)

        seq_after_update = await _get_last_event_sequence(
            rep_event_env, record_id
        )
        assert seq_after_update == seq_after_create

    async def test_delete_annotation_on_stale_base_no_event(
        self, rep_event_env: asyncpg.Pool
    ) -> None:
        (
            user_id,
            record_id,
            base_id,
            unit_id,
            anchor_segment_id,
            start_offset,
            end_offset,
            selected_text,
            text_hash,
            _,
        ) = await _setup_record(rep_event_env)

        anchor = _build_anchor(
            record_id=record_id,
            base_id=base_id,
            unit_id=unit_id,
            anchor_segment_id=anchor_segment_id,
            start_offset=start_offset,
            end_offset=end_offset,
            selected_text=selected_text,
            text_hash=text_hash,
        )
        req = UserAnnotationCreateRequest(
            anchor=anchor, selected_text=selected_text, color="warm_yellow"
        )
        response = await create_user_annotation(user_id, req)
        seq_after_create = await _get_last_event_sequence(
            rep_event_env, record_id
        )

        async with rep_event_env.acquire() as conn:
            await conn.execute(
                "UPDATE user_annotations SET generation = 999 WHERE id = $1",
                response.id,
            )

        await delete_user_annotation(user_id, response.id)

        seq_after_delete = await _get_last_event_sequence(
            rep_event_env, record_id
        )
        assert seq_after_delete == seq_after_create

    async def test_update_note_on_stale_base_no_event(
        self, rep_event_env: asyncpg.Pool
    ) -> None:
        (
            user_id,
            record_id,
            base_id,
            unit_id,
            anchor_segment_id,
            start_offset,
            end_offset,
            selected_text,
            text_hash,
            _,
        ) = await _setup_record(rep_event_env)

        anchor = _build_anchor(
            record_id=record_id,
            base_id=base_id,
            unit_id=unit_id,
            anchor_segment_id=anchor_segment_id,
            start_offset=start_offset,
            end_offset=end_offset,
            selected_text=selected_text,
            text_hash=text_hash,
        )
        note_req = ReaderNoteCreateRequest(
            anchor=anchor,
            selected_text=selected_text,
            note_text="Original note text for stale test.",
            quote_mode="text_range",
        )
        note_response = await create_reader_note(user_id, note_req)
        seq_after_create = await _get_last_event_sequence(
            rep_event_env, record_id
        )

        async with rep_event_env.acquire() as conn:
            await conn.execute(
                "UPDATE reader_notes SET generation = 999 WHERE id = $1",
                note_response.id,
            )

        await update_reader_note(
            user_id,
            note_response.id,
            ReaderNoteUpdateRequest(note_text="Updated text on stale base."),
        )

        seq_after_update = await _get_last_event_sequence(
            rep_event_env, record_id
        )
        assert seq_after_update == seq_after_create

    async def test_delete_note_on_stale_base_no_event(
        self, rep_event_env: asyncpg.Pool
    ) -> None:
        (
            user_id,
            record_id,
            base_id,
            unit_id,
            anchor_segment_id,
            start_offset,
            end_offset,
            selected_text,
            text_hash,
            _,
        ) = await _setup_record(rep_event_env)

        anchor = _build_anchor(
            record_id=record_id,
            base_id=base_id,
            unit_id=unit_id,
            anchor_segment_id=anchor_segment_id,
            start_offset=start_offset,
            end_offset=end_offset,
            selected_text=selected_text,
            text_hash=text_hash,
        )
        note_req = ReaderNoteCreateRequest(
            anchor=anchor,
            selected_text=selected_text,
            note_text="Note to be deleted on stale base.",
            quote_mode="text_range",
        )
        note_response = await create_reader_note(user_id, note_req)
        seq_after_create = await _get_last_event_sequence(
            rep_event_env, record_id
        )

        async with rep_event_env.acquire() as conn:
            await conn.execute(
                "UPDATE reader_notes SET generation = 999 WHERE id = $1",
                note_response.id,
            )

        await delete_reader_note(user_id, note_response.id)

        seq_after_delete = await _get_last_event_sequence(
            rep_event_env, record_id
        )
        assert seq_after_delete == seq_after_create

    async def test_delete_supplement_on_stale_base_no_event(
        self, rep_event_env: asyncpg.Pool
    ) -> None:
        (
            user_id,
            record_id,
            base_id,
            _unit_id,
            anchor_segment_id,
            start_offset,
            end_offset,
            selected_text,
            text_hash,
            _,
        ) = await _setup_record(rep_event_env)

        anchor = ReaderAskReadingRecordAnchor(
            record_id=str(record_id),
            base_id=str(base_id),
            generation=1,
            unit_id=_unit_id,
            anchor_segment_id=anchor_segment_id,
            start_offset=start_offset,
            end_offset=end_offset,
            selected_text=selected_text,
            text_hash=text_hash,
            hash_algorithm="fnv1a32-utf16",
        )
        candidate = ReaderAskSupplementCandidate(
            candidate_id=str(uuid4()),
            supplement_type="grammar_note",
            title="Stale fence supplement",
            content="This supplement content is long enough for the minimum length check.",
            target_key=f"reading-record:{record_id}:segment:{anchor_segment_id}:{start_offset}:{end_offset}",
            sentence_id=anchor_segment_id,
            schema_version="reader-ask-supplement-v1",
            created_from_turn_run_id=str(uuid4()),
            anchor=anchor,
        )
        row = await create_supplement(
            user_id=user_id,
            candidate=candidate,
        )
        supplement_id = row["id"]
        seq_after_create = await _get_last_event_sequence(
            rep_event_env, record_id
        )

        async with rep_event_env.acquire() as conn:
            await conn.execute(
                "UPDATE reader_ask_supplements SET generation = 999 WHERE id = $1",
                supplement_id,
            )

        await delete_supplement(user_id, supplement_id)

        seq_after_delete = await _get_last_event_sequence(
            rep_event_env, record_id
        )
        assert seq_after_delete == seq_after_create


# ---------------------------------------------------------------------------#
# Section 7: — Idempotent create no-op (reader_notes + highlight merge)
# ---------------------------------------------------------------------------#


class TestIdempotentCreateNoop:
    """Re-creating the same note or re-highlighting the same range must be
    a true no-op — no event, no sequence advancement."""

    async def test_reader_note_create_same_content_is_noop(
        self, rep_event_env: asyncpg.Pool
    ) -> None:
        """Creating a note with identical anchor + text as an existing note
        must NOT publish an event or advance the sequence."""
        (
            user_id,
            record_id,
            base_id,
            unit_id,
            anchor_segment_id,
            start_offset,
            end_offset,
            selected_text,
            text_hash,
            _,
        ) = await _setup_record(rep_event_env)

        anchor = _build_anchor(
            record_id=record_id,
            base_id=base_id,
            unit_id=unit_id,
            anchor_segment_id=anchor_segment_id,
            start_offset=start_offset,
            end_offset=end_offset,
            selected_text=selected_text,
            text_hash=text_hash,
        )
        note_req = ReaderNoteCreateRequest(
            anchor=anchor,
            selected_text=selected_text,
            note_text="Identical note text for idempotency test.",
            quote_mode="text_range",
        )
        await create_reader_note(user_id, note_req)
        seq_after_first = await _get_last_event_sequence(
            rep_event_env, record_id
        )

        # Create the exact same note again — must be a no-op.
        await create_reader_note(user_id, note_req)
        seq_after_second = await _get_last_event_sequence(
            rep_event_env, record_id
        )
        assert seq_after_second == seq_after_first

    async def test_reader_note_create_different_text_publishes_event(
        self, rep_event_env: asyncpg.Pool
    ) -> None:
        """Creating a note with the same anchor but different text must
        still publish an event (the ON CONFLICT WHERE is true)."""
        (
            user_id,
            record_id,
            base_id,
            unit_id,
            anchor_segment_id,
            start_offset,
            end_offset,
            selected_text,
            text_hash,
            _,
        ) = await _setup_record(rep_event_env)

        anchor = _build_anchor(
            record_id=record_id,
            base_id=base_id,
            unit_id=unit_id,
            anchor_segment_id=anchor_segment_id,
            start_offset=start_offset,
            end_offset=end_offset,
            selected_text=selected_text,
            text_hash=text_hash,
        )
        note_req_1 = ReaderNoteCreateRequest(
            anchor=anchor,
            selected_text=selected_text,
            note_text="First version of the note.",
            quote_mode="text_range",
        )
        await create_reader_note(user_id, note_req_1)
        seq_after_first = await _get_last_event_sequence(
            rep_event_env, record_id
        )

        note_req_2 = ReaderNoteCreateRequest(
            anchor=anchor,
            selected_text=selected_text,
            note_text="Second version with different text.",
            quote_mode="text_range",
        )
        await create_reader_note(user_id, note_req_2)
        seq_after_second = await _get_last_event_sequence(
            rep_event_env, record_id
        )
        assert seq_after_second == seq_after_first + 1

    async def test_highlight_rehighlight_same_range_color_is_noop(
        self, rep_event_env: asyncpg.Pool
    ) -> None:
        """Re-highlighting the exact same range with the same color must
        NOT publish an event or advance the sequence."""
        (
            user_id,
            record_id,
            base_id,
            unit_id,
            anchor_segment_id,
            start_offset,
            end_offset,
            selected_text,
            text_hash,
            _,
        ) = await _setup_record(rep_event_env)

        anchor = _build_anchor(
            record_id=record_id,
            base_id=base_id,
            unit_id=unit_id,
            anchor_segment_id=anchor_segment_id,
            start_offset=start_offset,
            end_offset=end_offset,
            selected_text=selected_text,
            text_hash=text_hash,
        )
        req = UserAnnotationCreateRequest(
            anchor=anchor, selected_text=selected_text, color="warm_yellow"
        )
        await create_user_annotation(user_id, req)
        seq_after_first = await _get_last_event_sequence(
            rep_event_env, record_id
        )

        # Re-highlight the exact same range + color — must be a no-op.
        await create_user_annotation(user_id, req)
        seq_after_second = await _get_last_event_sequence(
            rep_event_env, record_id
        )
        assert seq_after_second == seq_after_first

    async def test_highlight_rehighlight_same_range_different_color_publishes(
        self, rep_event_env: asyncpg.Pool
    ) -> None:
        """Re-highlighting the same range with a different color must
        still publish a merge event (color change = real change)."""
        (
            user_id,
            record_id,
            base_id,
            unit_id,
            anchor_segment_id,
            start_offset,
            end_offset,
            selected_text,
            text_hash,
            _,
        ) = await _setup_record(rep_event_env)

        anchor = _build_anchor(
            record_id=record_id,
            base_id=base_id,
            unit_id=unit_id,
            anchor_segment_id=anchor_segment_id,
            start_offset=start_offset,
            end_offset=end_offset,
            selected_text=selected_text,
            text_hash=text_hash,
        )
        req1 = UserAnnotationCreateRequest(
            anchor=anchor, selected_text=selected_text, color="warm_yellow"
        )
        await create_user_annotation(user_id, req1)
        seq_after_first = await _get_last_event_sequence(
            rep_event_env, record_id
        )

        req2 = UserAnnotationCreateRequest(
            anchor=anchor, selected_text=selected_text, color="soft_mint"
        )
        await create_user_annotation(user_id, req2)
        seq_after_second = await _get_last_event_sequence(
            rep_event_env, record_id
        )
        assert seq_after_second == seq_after_first + 1
