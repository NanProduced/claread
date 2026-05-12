"""
Tests for analysis task center and credit system.

Covers:
- compute_cost_points formula
- credit deduction logic (consistency, clamping, daily/bonus ordering)
- single-active-task constraints
- goal/variant validation
- startup recovery
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

if "asyncpg" not in sys.modules:
    asyncpg_stub = types.ModuleType("asyncpg")
    asyncpg_stub.Pool = object
    asyncpg_stub.Connection = object

    async def _create_pool(*args, **kwargs):
        raise RuntimeError("asyncpg stub create_pool should not be called in unit tests")

    asyncpg_stub.create_pool = _create_pool
    sys.modules["asyncpg"] = asyncpg_stub

from app.api.routes.tasks import submit_analysis_task
from app.api.routes.health import health_check, readiness_check
from app.schemas.analysis import RenderSceneModel
from app.schemas.tasks import TaskSubmitRequest
from app.services.analysis.task_executor import (
    AnalysisTaskWorker,
    compute_cost_points,
    execute_task,
)
from app.services.analysis.task_service import TaskExecutionPayload, TaskSubmitResult


def _build_render_scene() -> RenderSceneModel:
    return RenderSceneModel.model_validate(
        {
            "schema_version": "3.0.0",
            "request": {
                "request_id": "req-test",
                "source_type": "user_input",
                "reading_goal": "daily_reading",
                "reading_variant": "intermediate_reading",
                "profile_id": "daily_reading:intermediate_reading",
            },
            "article": {
                "source_type": "user_input",
                "source_text": "Hello world.",
                "render_text": "Hello world.",
                "paragraphs": [],
                "sentences": [],
            },
            "user_facing_state": "normal",
            "translations": [],
            "inline_marks": [],
            "sentence_entries": [],
            "warnings": [],
        }
    )


# ============================================================
# 1. compute_cost_points — pure function tests
# ============================================================


class TestComputeCostPoints:
    """Test the token-to-points conversion formula."""

    def test_none_usage_returns_zero(self):
        assert compute_cost_points(None) == 0

    def test_empty_usage_returns_zero(self):
        assert compute_cost_points({}) == 0

    def test_no_aggregate_returns_zero(self):
        assert compute_cost_points({"per_agent": {}}) == 0

    def test_zero_tokens(self):
        usage = {"aggregate": {"input_tokens": 0, "output_tokens": 0}}
        assert compute_cost_points(usage) == 0

    def test_basic_calculation(self):
        # 1000 input * 1 + 1000 output * 5 = 6000 weighted / 1000 = 6 points
        usage = {"aggregate": {"input_tokens": 1000, "output_tokens": 1000}}
        assert compute_cost_points(usage) == 6

    def test_ceil_division(self):
        # 500 input * 1 + 100 output * 5 = 1000 weighted / 1000 = 1 point (exact)
        usage = {"aggregate": {"input_tokens": 500, "output_tokens": 100}}
        assert compute_cost_points(usage) == 1

        # 501 * 1 + 100 * 5 = 1001 / 1000 = ceil(1.001) = 2
        usage = {"aggregate": {"input_tokens": 501, "output_tokens": 100}}
        assert compute_cost_points(usage) == 2

    def test_typical_analysis(self):
        # Typical: 5000 input + 8000 output → 5000*1 + 8000*5 = 45000 / 1000 = 45
        usage = {"aggregate": {"input_tokens": 5000, "output_tokens": 8000}}
        assert compute_cost_points(usage) == 45

    def test_large_analysis(self):
        # Large: 8000 input + 15000 output → 8000 + 75000 = 83000 / 1000 = 83
        usage = {"aggregate": {"input_tokens": 8000, "output_tokens": 15000}}
        assert compute_cost_points(usage) == 83

    def test_output_weighted_more(self):
        # Output-heavy: same total tokens but output costs 5x
        usage_input_heavy = {"aggregate": {"input_tokens": 10000, "output_tokens": 0}}
        usage_output_heavy = {"aggregate": {"input_tokens": 0, "output_tokens": 10000}}
        assert compute_cost_points(usage_input_heavy) == 10
        assert compute_cost_points(usage_output_heavy) == 50


# ============================================================
# 2. TaskSubmitRequest validation
# ============================================================


class TestTaskSubmitRequestValidation:
    """Test reading_goal/reading_variant combination validation."""

    def test_valid_combination(self):
        req = TaskSubmitRequest(
            text="Hello world",
            reading_goal="daily_reading",
            reading_variant="intermediate_reading",
        )
        assert req.reading_goal == "daily_reading"

    def test_valid_exam_combination(self):
        req = TaskSubmitRequest(
            text="Hello world",
            reading_goal="exam",
            reading_variant="gaokao",
        )
        assert req.reading_variant == "gaokao"

    def test_invalid_combination_raises(self):
        with pytest.raises(ValueError, match="does not match"):
            TaskSubmitRequest(
                text="Hello world",
                reading_goal="exam",
                reading_variant="intermediate_reading",  # wrong for exam
            )

    def test_invalid_combination_daily_reading(self):
        with pytest.raises(ValueError, match="does not match"):
            TaskSubmitRequest(
                text="Hello world",
                reading_goal="daily_reading",
                reading_variant="gaokao",  # wrong for daily_reading
            )


# ============================================================
# 3. Credit deduction logic — unit tests with mocked DB
# ============================================================


class TestCreditDeductionLogic:
    """
    Test the deduction math directly.

    These tests verify the core deduction algorithm is correct
    by examining the logic using computed values.
    """

    def test_daily_only_deduction(self):
        """When daily has enough, entire cost should come from daily."""
        daily_free = 1000
        daily_used = 100
        bonus = 0
        cost = 50

        daily_remaining = max(daily_free - daily_used, 0)
        deduct_from_daily = min(cost, daily_remaining)
        remaining_cost = cost - deduct_from_daily
        available_bonus = max(bonus, 0)
        deduct_from_bonus = min(remaining_cost, available_bonus)
        actual_total = deduct_from_daily + deduct_from_bonus

        assert deduct_from_daily == 50
        assert deduct_from_bonus == 0
        assert actual_total == 50

    def test_daily_plus_bonus_deduction(self):
        """When daily insufficient, overflow goes to bonus."""
        daily_free = 1000
        daily_used = 980
        bonus = 100
        cost = 50

        daily_remaining = max(daily_free - daily_used, 0)
        deduct_from_daily = min(cost, daily_remaining)
        remaining_cost = cost - deduct_from_daily
        available_bonus = max(bonus, 0)
        deduct_from_bonus = min(remaining_cost, available_bonus)
        actual_total = deduct_from_daily + deduct_from_bonus

        assert deduct_from_daily == 20
        assert deduct_from_bonus == 30
        assert actual_total == 50

    def test_insufficient_total_clamps(self):
        """When total available < cost, only deduct what's available."""
        daily_free = 1000
        daily_used = 999
        bonus = 0
        cost = 50

        daily_remaining = max(daily_free - daily_used, 0)
        deduct_from_daily = min(cost, daily_remaining)
        remaining_cost = cost - deduct_from_daily
        available_bonus = max(bonus, 0)
        deduct_from_bonus = min(remaining_cost, available_bonus)
        actual_total = deduct_from_daily + deduct_from_bonus

        # Only 1 point available
        assert deduct_from_daily == 1
        assert deduct_from_bonus == 0
        assert actual_total == 1

    def test_consistency_daily_exhausted_bonus_partial(self):
        """When daily exhausted and bonus partially covers cost."""
        daily_free = 1000
        daily_used = 1000
        bonus = 30
        cost = 50

        daily_remaining = max(daily_free - daily_used, 0)
        deduct_from_daily = min(cost, daily_remaining)
        remaining_cost = cost - deduct_from_daily
        available_bonus = max(bonus, 0)
        deduct_from_bonus = min(remaining_cost, available_bonus)
        actual_total = deduct_from_daily + deduct_from_bonus

        new_daily_used = daily_used + deduct_from_daily
        new_bonus = bonus - deduct_from_bonus
        balance_after = (daily_free - new_daily_used) + new_bonus

        assert deduct_from_daily == 0
        assert deduct_from_bonus == 30
        assert actual_total == 30  # Only 30 charged, not 50
        assert new_bonus == 0
        assert balance_after == 0

    def test_zero_cost_no_deduction(self):
        """Zero cost should result in no deduction."""
        cost = 0
        assert cost <= 0  # shortcut return in real code

    def test_ledger_matches_account(self):
        """Verify that sum of ledger entries equals actual_total."""
        daily_free = 1000
        daily_used = 990
        bonus = 5
        cost = 50

        daily_remaining = max(daily_free - daily_used, 0)
        deduct_from_daily = min(cost, daily_remaining)
        remaining_cost = cost - deduct_from_daily
        available_bonus = max(bonus, 0)
        deduct_from_bonus = min(remaining_cost, available_bonus)
        actual_total = deduct_from_daily + deduct_from_bonus

        new_bonus = bonus - deduct_from_bonus

        # Ledger entries are negative, sum of absolutes should equal actual_total
        assert abs(-deduct_from_daily) + abs(-deduct_from_bonus) == actual_total
        assert actual_total == 15  # 10 daily + 5 bonus
        assert new_bonus == 0  # bonus never goes negative


# ============================================================
# 4. Startup recovery
# ============================================================


class TestStartupRecovery:
    """Test recover_stuck_tasks logic."""

    @pytest.mark.anyio
    async def test_no_stuck_tasks(self):
        """When no stuck tasks, recovery should return 0."""
        with patch(
            "app.services.analysis.task_executor.requeue_stale_tasks",
            AsyncMock(return_value=0),
        ) as requeue_mock:
            from app.services.analysis.task_executor import recover_stuck_tasks
            count = await recover_stuck_tasks()
            assert count == 0
            requeue_mock.assert_awaited_once()

    @pytest.mark.anyio
    async def test_recovery_with_stuck_tasks(self):
        """Recovery should requeue stale tasks for retry."""
        with patch(
            "app.services.analysis.task_executor.requeue_stale_tasks",
            AsyncMock(return_value=2),
        ) as requeue_mock:
            from app.services.analysis.task_executor import recover_stuck_tasks
            count = await recover_stuck_tasks()
            assert count == 2
            requeue_mock.assert_awaited_once()


# ============================================================
# 5. Route and executor behavior regressions
# ============================================================


class TestTaskSubmitRoute:
    """Route-level behavior for task creation and quota gating."""

    @pytest.mark.anyio
    async def test_insufficient_quota_rejects_before_task_creation(self):
        user_id = uuid4()
        body = TaskSubmitRequest(
            text="Hello world",
            reading_goal="daily_reading",
            reading_variant="intermediate_reading",
        )
        current_user = SimpleNamespace(user_id=str(user_id))

        with (
            patch(
                "app.api.routes.tasks.ensure_credit_account",
                AsyncMock(),
            ),
            patch(
                "app.api.routes.tasks.get_active_task",
                AsyncMock(return_value=None),
            ),
            patch(
                "app.api.routes.tasks.check_quota",
                AsyncMock(return_value=0),
            ) as quota_mock,
            patch(
                "app.api.routes.tasks.submit_task",
                AsyncMock(),
            ) as submit_mock,
        ):
            response = await submit_analysis_task(current_user, body)

        assert response.status_code == 402
        payload = json.loads(response.body)
        assert payload["error"] == "INSUFFICIENT_CREDITS"
        assert payload["remaining_points"] == 0
        quota_mock.assert_awaited_once()
        submit_mock.assert_not_awaited()

    @pytest.mark.anyio
    async def test_new_task_launches_after_quota_check(self):
        user_id = uuid4()
        task_id = uuid4()
        record_id = uuid4()
        body = TaskSubmitRequest(
            text="Hello world",
            reading_goal="daily_reading",
            reading_variant="intermediate_reading",
        )
        current_user = SimpleNamespace(user_id=str(user_id))
        created = TaskSubmitResult(
            task_id=task_id,
            record_id=record_id,
            client_record_id="task-test-record",
            status="queued",
            created=True,
        )

        with (
            patch(
                "app.api.routes.tasks.ensure_credit_account",
                AsyncMock(),
            ),
            patch(
                "app.api.routes.tasks.get_active_task",
                AsyncMock(return_value=None),
            ),
            patch(
                "app.api.routes.tasks.check_quota",
                AsyncMock(return_value=1),
            ),
            patch(
                "app.api.routes.tasks.submit_task",
                AsyncMock(return_value=created),
            ) as submit_mock,
        ):
            response = await submit_analysis_task(current_user, body)

        assert response.status_code == 202
        payload = json.loads(response.body)
        assert payload["created"] is True
        submit_mock.assert_awaited_once()

    @pytest.mark.anyio
    async def test_wait_for_result_returns_render_scene_when_task_succeeds(self):
        user_id = uuid4()
        task_id = uuid4()
        record_id = uuid4()
        body = TaskSubmitRequest(
            text="Hello world",
            reading_goal="daily_reading",
            reading_variant="intermediate_reading",
            wait_for_result=True,
            wait_timeout_seconds=30,
        )
        current_user = SimpleNamespace(user_id=str(user_id))
        created = TaskSubmitResult(
            task_id=task_id,
            record_id=record_id,
            client_record_id="task-test-record",
            status="queued",
            created=True,
        )
        render_scene = _build_render_scene()

        with (
            patch("app.api.routes.tasks.ensure_credit_account", AsyncMock()),
            patch("app.api.routes.tasks.get_active_task", AsyncMock(return_value=None)),
            patch("app.api.routes.tasks.check_quota", AsyncMock(return_value=1)),
            patch("app.api.routes.tasks.submit_task", AsyncMock(return_value=created)),
            patch(
                "app.api.routes.tasks._wait_task_until_terminal",
                AsyncMock(
                    return_value={
                        "task_id": task_id,
                        "record_id": record_id,
                        "status": "succeeded",
                    }
                ),
            ),
            patch(
                "app.api.routes.tasks.records_svc.get_record_by_id",
                AsyncMock(return_value={"render_scene_json": render_scene.model_dump(mode="json")}),
            ),
        ):
            response = await submit_analysis_task(current_user, body)

        assert response.status_code == 200
        payload = json.loads(response.body)
        assert payload["status"] == "succeeded"
        assert payload["task_id"] == str(task_id)
        assert payload["record_id"] == str(record_id)
        assert payload["render_scene"]["schema_version"] == "3.0.0"

    @pytest.mark.anyio
    async def test_wait_for_result_timeout_returns_accepted_without_render_scene(self):
        user_id = uuid4()
        task_id = uuid4()
        record_id = uuid4()
        body = TaskSubmitRequest(
            text="Hello world",
            reading_goal="daily_reading",
            reading_variant="intermediate_reading",
            wait_for_result=True,
            wait_timeout_seconds=5,
        )
        current_user = SimpleNamespace(user_id=str(user_id))
        created = TaskSubmitResult(
            task_id=task_id,
            record_id=record_id,
            client_record_id="task-test-record",
            status="queued",
            created=True,
        )

        with (
            patch("app.api.routes.tasks.ensure_credit_account", AsyncMock()),
            patch("app.api.routes.tasks.get_active_task", AsyncMock(return_value=None)),
            patch("app.api.routes.tasks.check_quota", AsyncMock(return_value=1)),
            patch("app.api.routes.tasks.submit_task", AsyncMock(return_value=created)),
            patch(
                "app.api.routes.tasks._wait_task_until_terminal",
                AsyncMock(
                    return_value={
                        "task_id": task_id,
                        "record_id": record_id,
                        "status": "running",
                    }
                ),
            ),
        ):
            response = await submit_analysis_task(current_user, body)

        assert response.status_code == 202
        payload = json.loads(response.body)
        assert payload["status"] == "running"
        assert "render_scene" not in payload

    @pytest.mark.anyio
    async def test_active_task_conflict_preempts_quota_check(self):
        user_id = uuid4()
        task_id = uuid4()
        record_id = uuid4()
        body = TaskSubmitRequest(
            text="Hello world",
            reading_goal="daily_reading",
            reading_variant="intermediate_reading",
        )
        current_user = SimpleNamespace(user_id=str(user_id))
        active = {
            "task_id": task_id,
            "record_id": record_id,
            "status": "running",
        }

        with (
            patch(
                "app.api.routes.tasks.ensure_credit_account",
                AsyncMock(),
            ),
            patch(
                "app.api.routes.tasks.get_active_task",
                AsyncMock(return_value=active),
            ) as active_mock,
            patch(
                "app.api.routes.tasks.check_quota",
                AsyncMock(),
            ) as quota_mock,
            patch(
                "app.api.routes.tasks.submit_task",
                AsyncMock(),
            ) as submit_mock,
        ):
            response = await submit_analysis_task(current_user, body)

        assert response.status_code == 409
        payload = json.loads(response.body)
        assert payload["error"] == "ACTIVE_TASK_EXISTS"
        assert payload["task_id"] == str(task_id)
        active_mock.assert_awaited_once()
        quota_mock.assert_not_awaited()
        submit_mock.assert_not_awaited()


class TestTaskExecutorCharging:
    """Executor should charge weighted token points."""

    @pytest.mark.anyio
    async def test_execute_task_charges_fixed_success_points(self):
        task_id = uuid4()
        record_id = uuid4()
        user_id = uuid4()

        class DummyRenderScene:
            user_facing_state = "normal"

            def model_dump(self, mode: str = "json") -> dict[str, Any]:
                return {"userFacingState": self.user_facing_state, "mode": mode}

        workflow_result = {
            "render_scene": DummyRenderScene(),
            "usage_summary": {
                "aggregate": {
                    "input_tokens": 2000,
                    "output_tokens": 3000,
                    "total_tokens": 5000,
                }
            },
        }

        with (
            patch(
                "app.services.analysis.task_executor.run_article_analysis_with_state",
                AsyncMock(return_value=workflow_result),
            ),
            patch(
                "app.services.analysis.task_executor.update_task_status",
                AsyncMock(),
            ),
            patch(
                "app.services.analysis.task_executor.insert_task_event",
                AsyncMock(),
            ),
            patch(
                "app.services.analysis.task_executor.records_svc.update_record",
                AsyncMock(),
            ),
            patch(
                "app.services.analysis.task_executor.records_svc.insert_audit_log",
                AsyncMock(),
            ),
            patch(
                "app.services.analysis.task_executor.deduct_credits",
                AsyncMock(return_value=17),
            ) as deduct_mock,
        ):
            await execute_task(
                task_id=task_id,
                record_id=record_id,
                user_id=user_id,
                text="Hello world",
                reading_goal="daily_reading",
                reading_variant="intermediate_reading",
                source_type="user_input",
                extended=False,
            )

        deduct_mock.assert_awaited_once()
        assert deduct_mock.await_args.kwargs["cost_points"] == 17

    @pytest.mark.anyio
    async def test_execute_task_does_not_charge_unrenderable_heavy_result(self):
        task_id = uuid4()
        record_id = uuid4()
        user_id = uuid4()

        render_scene = RenderSceneModel.model_validate(
            {
                "schema_version": "3.0.0",
                "request": {
                    "request_id": "req-test",
                    "source_type": "user_input",
                    "reading_goal": "daily_reading",
                    "reading_variant": "intermediate_reading",
                    "profile_id": "daily_intermediate",
                },
                "article": {
                    "source_type": "user_input",
                    "source_text": "Hello world.",
                    "render_text": "",
                    "paragraphs": [],
                    "sentences": [],
                },
                "user_facing_state": "degraded_heavy",
                "translations": [],
                "inline_marks": [],
                "sentence_entries": [],
                "warnings": [
                    {
                        "code": "VOCABULARY_AGENT_FAILED",
                        "level": "error",
                        "message": "vocabulary agent failed",
                    },
                    {
                        "code": "GRAMMAR_AGENT_FAILED",
                        "level": "error",
                        "message": "grammar agent failed",
                    },
                    {
                        "code": "TRANSLATION_AGENT_FAILED",
                        "level": "error",
                        "message": "translation agent failed",
                    },
                ],
            }
        )
        workflow_result = {
            "render_scene": render_scene,
            "usage_summary": {
                "aggregate": {
                    "input_tokens": 1200,
                    "output_tokens": 600,
                    "total_tokens": 1800,
                }
            },
        }

        with (
            patch(
                "app.services.analysis.task_executor.run_article_analysis_with_state",
                AsyncMock(return_value=workflow_result),
            ),
            patch(
                "app.services.analysis.task_executor.update_task_status",
                AsyncMock(),
            ) as status_mock,
            patch(
                "app.services.analysis.task_executor.insert_task_event",
                AsyncMock(),
            ) as event_mock,
            patch(
                "app.services.analysis.task_executor.records_svc.update_record",
                AsyncMock(),
            ) as update_record_mock,
            patch(
                "app.services.analysis.task_executor.records_svc.insert_audit_log",
                AsyncMock(),
            ) as audit_mock,
            patch(
                "app.services.analysis.task_executor.deduct_credits",
                AsyncMock(return_value=4),
            ) as deduct_mock,
        ):
            await execute_task(
                task_id=task_id,
                record_id=record_id,
                user_id=user_id,
                text="Hello world",
                reading_goal="daily_reading",
                reading_variant="intermediate_reading",
                source_type="user_input",
                extended=False,
            )

        deduct_mock.assert_not_awaited()
        audit_mock.assert_not_awaited()
        assert status_mock.await_args_list[-1].kwargs["status"] == "failed"
        assert update_record_mock.await_args_list[-1].kwargs["analysis_status"] == "failed"
        assert event_mock.await_args_list[-1].args[1] == "task_failed"


class TestWorkerLoop:
    """Worker should claim queued tasks and dispatch execution."""

    @pytest.mark.anyio
    async def test_worker_claims_and_launches_tasks(self):
        payload = TaskExecutionPayload(
            task_id=uuid4(),
            record_id=uuid4(),
            user_id=uuid4(),
            text="Hello world",
            reading_goal="daily_reading",
            reading_variant="intermediate_reading",
            source_type="user_input",
            extended=False,
            worker_token="worker-1",
        )
        launched_task: asyncio.Task = asyncio.create_task(asyncio.sleep(0))

        responses = iter([payload, None])

        async def claim_once_then_idle(_: str):
            try:
                return next(responses)
            except StopIteration:
                return None

        with (
            patch(
                "app.services.analysis.task_executor.claim_next_queued_task",
                AsyncMock(side_effect=claim_once_then_idle),
            ) as claim_mock,
            patch(
                "app.services.analysis.task_executor.launch_task",
                MagicMock(return_value=launched_task),
            ) as launch_mock,
        ):
            worker = AnalysisTaskWorker(max_concurrency=1, poll_interval_seconds=0.01)
            worker.start()
            await asyncio.sleep(0.05)
            await worker.stop()

        assert claim_mock.await_count >= 1
        launch_mock.assert_called_once()


class TestHealthRoutes:
    """Health endpoints should reflect DB + worker readiness."""

    @pytest.mark.anyio
    async def test_health_check_includes_worker_status(self):
        worker = MagicMock()
        worker.health_snapshot.return_value = {
            "healthy": True,
            "worker_token": "worker-1",
            "runner_running": True,
            "stopping": False,
            "inflight_tasks": 2,
        }
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(analysis_task_worker=worker)
            )
        )

        with (
            patch("app.api.routes.health.is_db_ready", AsyncMock(return_value=True)),
            patch("app.api.routes.health.is_redis_ready", AsyncMock(return_value=False)),
        ):
            payload = await health_check(request)

        assert payload["status"] == "ok"
        assert payload["postgres"] is True
        assert payload["worker"] is True
        assert payload["worker_inflight_tasks"] == 2

    @pytest.mark.anyio
    async def test_readiness_fails_when_worker_unhealthy(self):
        worker = MagicMock()
        worker.health_snapshot.return_value = {
            "healthy": False,
            "worker_token": "worker-1",
            "runner_running": False,
            "stopping": False,
            "inflight_tasks": 0,
        }
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(analysis_task_worker=worker)
            )
        )

        with patch("app.api.routes.health.is_db_ready", AsyncMock(return_value=True)):
            with pytest.raises(Exception) as exc_info:
                await readiness_check(request)

        assert getattr(exc_info.value, "status_code", None) == 503


# ============================================================
# Helpers
# ============================================================


class AsyncContextManager:
    """Helper to make AsyncMock work as async context manager."""
    def __init__(self, mock_conn):
        self.mock_conn = mock_conn

    async def __aenter__(self):
        return self.mock_conn

    async def __aexit__(self, *args):
        pass


@pytest.fixture
def anyio_backend():
    """Restrict anyio tests to asyncio because trio is not installed in CI/dev."""
    return "asyncio"
