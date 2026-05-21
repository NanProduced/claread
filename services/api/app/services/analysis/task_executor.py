"""
Analysis Task Executor.

Runs queued analysis tasks from the database in a background worker loop,
writes results back to analysis_records + analysis_results,
and deducts credits on success only.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
import logging
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4

from app.config.settings import get_settings
from app.llm.routes import MODEL_ROUTE_ANNOTATION_GENERATION
from app.schemas.analysis import AnalyzeRequest
from app.services.ai_usage import (
    AIUsageEventCreate,
    ANALYSIS_WEIGHTED_TOKENS_POLICY_VERSION,
    BILLING_MODE_USER_POINTS,
    CAPABILITY_ANALYSIS_FULL,
    STATUS_FAILED,
    STATUS_SUCCEEDED,
    USAGE_SCOPE_USER_BILLED,
    build_analysis_billing_metadata,
    compute_analysis_cost_points,
    extract_request_id_from_render_scene,
    extract_schema_version_from_render_scene,
    record_ai_usage_event,
    resolve_model_metadata,
)
from app.services.analysis.credit_service import deduct_credits
from app.services.analysis.prompting.prompt_loader import get_prompt_version
from app.services.analysis.task_service import (
    TaskExecutionPayload,
    claim_next_queued_task,
    insert_task_event,
    requeue_stale_tasks,
    touch_task_heartbeat,
    update_task_status,
)
from app.services.user_assets import records as records_svc
from app.workflow.analyze import (
    ANALYZE_SCHEMA_VERSION,
    WORKFLOW_NAME,
    WORKFLOW_VERSION,
    run_article_analysis_with_state,
)

logger = logging.getLogger(__name__)

# Worker behavior
QUEUED_STALE_AFTER = timedelta(minutes=5)
ACTIVE_STALE_AFTER = timedelta(minutes=5)
MAX_CONCURRENT_TASKS = 4
CLAIM_POLL_INTERVAL_SECONDS = 1.0
SHUTDOWN_WAIT_SECONDS = 5.0
TASK_HEARTBEAT_INTERVAL_SECONDS = 30.0

HEAVY_FAILURE_CODES = {
    "VOCABULARY_AGENT_FAILED",
    "GRAMMAR_AGENT_FAILED",
    "TRANSLATION_AGENT_FAILED",
    "NORMALIZE_AND_GROUND_FAILED",
    "TERM_AGENT_FAILED",
    "ACADEMIC_TRANSLATION_AGENT_FAILED",
    "UNDERSTANDING_AGENT_FAILED",
    "ACADEMIC_NORMALIZE_FAILED",
}


class UnrenderableAnalysisError(RuntimeError):
    """Raised when workflow output cannot produce a user-visible result."""


def compute_cost_points(usage_summary: dict[str, Any] | None) -> int:
    """Backward-compatible wrapper around the capability billing policy."""
    return compute_analysis_cost_points(usage_summary)


def _build_deduction_metadata(usage_summary: dict[str, Any] | None) -> dict[str, Any]:
    """Build metadata dict for credit ledger entry."""
    return build_analysis_billing_metadata(usage_summary)


def _extract_list(payload: dict[str, Any], *keys: str) -> list[Any]:
    """Read a list field from either snake_case or camelCase keys."""
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def _has_renderable_content(render_scene_dict: dict[str, Any]) -> bool:
    """Check whether a render scene contains user-visible body content."""
    article = render_scene_dict.get("article")
    if not isinstance(article, dict):
        return False

    render_text = article.get("render_text") or article.get("renderText") or ""
    if isinstance(render_text, str) and render_text.strip():
        return True

    paragraphs = _extract_list(article, "paragraphs")
    if any(
        isinstance(paragraph, dict)
        and (
            str(paragraph.get("text") or "").strip()
            or _extract_list(paragraph, "sentence_ids", "sentenceIds")
        )
        for paragraph in paragraphs
    ):
        return True

    sentences = _extract_list(article, "sentences")
    return any(
        isinstance(sentence, dict) and str(sentence.get("text") or "").strip()
        for sentence in sentences
    )


def _is_unrenderable_failure(
    render_scene_dict: dict[str, Any],
    user_facing_state: str | None,
) -> bool:
    """
    Treat heavy-degraded empty scenes as failures.

    This avoids charging users for runs where some agents spent tokens but
    the workflow still could not produce a renderable result page.
    """
    warning_codes = {
        str(warning.get("code"))
        for warning in _extract_list(render_scene_dict, "warnings")
        if isinstance(warning, dict) and warning.get("code")
    }
    has_heavy_failure = user_facing_state == "degraded_heavy" or bool(
        warning_codes & HEAVY_FAILURE_CODES
    )
    has_annotations = bool(_extract_list(render_scene_dict, "inline_marks", "inlineMarks")) or bool(
        _extract_list(render_scene_dict, "sentence_entries", "sentenceEntries")
    )
    has_translations = bool(_extract_list(render_scene_dict, "translations"))

    return has_heavy_failure and not (
        _has_renderable_content(render_scene_dict)
        or has_annotations
        or has_translations
    )


async def execute_task(
    task_id: UUID,
    record_id: UUID,
    user_id: UUID,
    text: str,
    reading_goal: str,
    reading_variant: str,
    source_type: str,
    extended: bool,
    *,
    worker_token: str | None = None,
    already_claimed: bool = False,
) -> None:
    """
    Execute analysis task.
    """
    heartbeat_task: asyncio.Task | None = None
    start_time = datetime.now(timezone.utc)
    start_perf = perf_counter()
    usage_summary: dict[str, Any] | None = None
    payload: AnalyzeRequest | None = None
    render_scene_dict: dict[str, Any] | None = None
    request_id: str | None = None
    model_metadata = resolve_model_metadata(get_settings(), MODEL_ROUTE_ANNOTATION_GENERATION)

    try:
        active_worker_token = worker_token or f"worker-{uuid4()}"

        if already_claimed:
            await insert_task_event(
                task_id,
                "task_started",
                {"worker_token": active_worker_token},
            )
        else:
            await update_task_status(
                task_id,
                status="running",
                started_at=start_time,
                worker_token=active_worker_token,
            )
            await insert_task_event(
                task_id,
                "task_started",
                {"worker_token": active_worker_token},
            )

        heartbeat_task = asyncio.create_task(
            _heartbeat_loop(task_id, active_worker_token),
            name=f"analysis-task-heartbeat-{task_id}",
        )

        payload = AnalyzeRequest(
            text=text,
            reading_goal=reading_goal,
            reading_variant=reading_variant,
            source_type=source_type,
            extended=extended,
        )

        result = await run_article_analysis_with_state(payload)

        render_scene = result.get("render_scene")
        usage_summary = result.get("usage_summary")
        translation_draft = result.get("translation_draft")

        record_title: str | None = None
        if isinstance(translation_draft, dict):
            maybe_title = translation_draft.get("title")
            if isinstance(maybe_title, str):
                record_title = maybe_title.strip() or None
        elif translation_draft is not None:
            maybe_title = getattr(translation_draft, "title", None)
            if isinstance(maybe_title, str):
                record_title = maybe_title.strip() or None

        if record_title and len(record_title) > 256:
            record_title = record_title[:256]

        if render_scene is None:
            raise RuntimeError("Workflow returned no render_scene")

        render_scene_dict = (
            render_scene.model_dump(mode="json")
            if hasattr(render_scene, "model_dump")
            else render_scene
        )
        request_id = extract_request_id_from_render_scene(render_scene_dict)
        user_facing_state = getattr(render_scene, "user_facing_state", "normal")
        if (
            isinstance(render_scene_dict, dict)
            and _is_unrenderable_failure(render_scene_dict, user_facing_state)
        ):
            raise UnrenderableAnalysisError(
                "Workflow produced no renderable content after heavy degradation"
            )
        cost_points = compute_cost_points(usage_summary)

        await update_task_status(task_id, status="finalizing")
        await insert_task_event(
            task_id,
            "task_finalizing",
            {"cost_points": cost_points},
        )

        # 1. Update Record and Split Result Content
        await records_svc.update_record(
            user_id=user_id,
            record_id=record_id,
            analysis_status="ready",
            title=record_title,
            render_scene_json=render_scene_dict,
            page_state_json={"pageState": user_facing_state},
            user_facing_state=user_facing_state,
            workflow_version=WORKFLOW_VERSION,
            schema_version=render_scene_dict.get("schema_version", ANALYZE_SCHEMA_VERSION),
        )

        # 2. Deduct Credits
        actual_deducted = 0
        if cost_points > 0:
            actual_deducted = await deduct_credits(
                user_id=user_id,
                task_id=task_id,
                cost_points=cost_points,
                metadata=_build_deduction_metadata(usage_summary),
            )

        await record_ai_usage_event(
            AIUsageEventCreate(
                usage_scope=USAGE_SCOPE_USER_BILLED,
                capability_code=CAPABILITY_ANALYSIS_FULL,
                billing_mode=BILLING_MODE_USER_POINTS,
                status=STATUS_SUCCEEDED,
                user_id=user_id,
                task_id=task_id,
                record_id=record_id,
                request_id=request_id,
                workflow_name=WORKFLOW_NAME,
                workflow_version=WORKFLOW_VERSION,
                schema_version=extract_schema_version_from_render_scene(render_scene_dict)
                or ANALYZE_SCHEMA_VERSION,
                prompt_version=get_prompt_version(),
                usage_data=usage_summary,
                latency_ms=int((perf_counter() - start_perf) * 1000),
                billed_points=actual_deducted,
                billing_policy_version=ANALYSIS_WEIGHTED_TOKENS_POLICY_VERSION,
                metadata_json={
                    "entrypoint": "/analysis-tasks",
                    "task_execution_mode": "worker",
                    "source_type": source_type,
                    "reading_goal": reading_goal,
                    "reading_variant": reading_variant,
                    "extended": extended,
                    "user_facing_state": user_facing_state,
                    "computed_cost_points": cost_points,
                },
                **model_metadata,
            )
        )

        # 3. Increment Achievement Stats
        await records_svc.increment_user_reading_count(user_id)

        finished_at = datetime.now(timezone.utc)
        await update_task_status(
            task_id,
            status="succeeded",
            finished_at=finished_at,
            usage_summary_json=usage_summary or {},
            quota_cost_points=actual_deducted,
        )
        await insert_task_event(
            task_id,
            "task_succeeded",
            {
                "cost_points": actual_deducted,
                "usage_summary": usage_summary or {},
            },
        )

        logger.info(
            "Task %s succeeded (record=%s, cost=%d points)",
            task_id,
            record_id,
            actual_deducted,
        )

    except Exception as exc:
        logger.exception("Task %s failed: %s", task_id, exc)

        failure_code = type(exc).__name__
        failure_message = str(exc)[:500]

        try:
            await update_task_status(
                task_id,
                status="failed",
                finished_at=datetime.now(timezone.utc),
                failure_code=failure_code,
                failure_message=failure_message,
                usage_summary_json=usage_summary or {},
            )
            await records_svc.update_record(
                user_id=user_id,
                record_id=record_id,
                analysis_status="failed",
                user_facing_state="failed"
            )

            cost_points = compute_cost_points(usage_summary) if usage_summary else 0

            await record_ai_usage_event(
                AIUsageEventCreate(
                    usage_scope=USAGE_SCOPE_USER_BILLED,
                    capability_code=CAPABILITY_ANALYSIS_FULL,
                    billing_mode=BILLING_MODE_USER_POINTS,
                    status=STATUS_FAILED,
                    user_id=user_id,
                    task_id=task_id,
                    record_id=record_id,
                    request_id=request_id
                    or extract_request_id_from_render_scene(render_scene_dict or {}),
                    workflow_name=WORKFLOW_NAME,
                    workflow_version=WORKFLOW_VERSION,
                    schema_version=extract_schema_version_from_render_scene(render_scene_dict or {})
                    or ANALYZE_SCHEMA_VERSION,
                    prompt_version=get_prompt_version(),
                    usage_data=usage_summary,
                    latency_ms=int((perf_counter() - start_perf) * 1000),
                    billed_points=0,
                    billing_policy_version=ANALYSIS_WEIGHTED_TOKENS_POLICY_VERSION,
                    error_code=failure_code,
                    error_message=failure_message,
                    metadata_json={
                        "entrypoint": "/analysis-tasks",
                        "task_execution_mode": "worker",
                        "source_type": source_type,
                        "reading_goal": reading_goal,
                        "reading_variant": reading_variant,
                        "extended": extended,
                        "computed_cost_points": cost_points,
                        "record_status_after_failure": "failed",
                    },
                    **model_metadata,
                )
            )

            await insert_task_event(
                task_id,
                "task_failed",
                {
                    "failure_code": failure_code,
                    "failure_message": failure_message,
                    "usage_summary": usage_summary or {},
                    "cost_points": cost_points,
                },
            )
        except Exception as inner_exc:
            logger.exception(
                "Failed to update task %s status after failure: %s",
                task_id,
                inner_exc,
            )
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task


def launch_task(payload: TaskExecutionPayload) -> asyncio.Task:
    """Launch a claimed task payload in the background."""
    return asyncio.create_task(
        execute_task(
            task_id=payload.task_id,
            record_id=payload.record_id,
            user_id=payload.user_id,
            text=payload.text,
            reading_goal=payload.reading_goal,
            reading_variant=payload.reading_variant,
            source_type=payload.source_type,
            extended=payload.extended,
            worker_token=payload.worker_token,
            already_claimed=True,
        ),
        name=f"analysis-task-{payload.task_id}",
    )


class AnalysisTaskWorker:
    """Database-backed worker that claims and executes queued analysis tasks."""

    def __init__(
        self,
        *,
        max_concurrency: int = MAX_CONCURRENT_TASKS,
        poll_interval_seconds: float = CLAIM_POLL_INTERVAL_SECONDS,
    ) -> None:
        self.max_concurrency = max_concurrency
        self.poll_interval_seconds = poll_interval_seconds
        self.worker_token = f"analysis-worker-{uuid4()}"
        self._runner: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._inflight: set[asyncio.Task] = set()

    def start(self) -> asyncio.Task:
        """Start the worker loop once."""
        if self._runner is None:
            self._runner = asyncio.create_task(
                self.run_forever(),
                name="analysis-task-worker",
            )
            self._runner.add_done_callback(self._on_runner_done)
        return self._runner

    async def stop(self) -> None:
        """Stop polling and wait briefly for in-flight tasks to settle."""
        self._stop_event.set()
        if self._runner is not None:
            await self._runner
            self._runner = None
        if self._inflight:
            await asyncio.wait(self._inflight, timeout=SHUTDOWN_WAIT_SECONDS)

    async def run_forever(self) -> None:
        """Poll the task table, claim work, and fan out execution tasks."""
        while not self._stop_event.is_set():
            claimed_any = False

            while (
                not self._stop_event.is_set()
                and len(self._inflight) < self.max_concurrency
            ):
                payload = await claim_next_queued_task(self.worker_token)
                if payload is None:
                    break

                claimed_any = True
                task = launch_task(payload)
                self._inflight.add(task)
                task.add_done_callback(self._inflight.discard)

            if self._stop_event.is_set():
                break

            timeout = 0 if claimed_any else self.poll_interval_seconds
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                pass

    def health_snapshot(self) -> dict[str, Any]:
        """Return current worker health for diagnostics and readiness checks."""
        runner_running = self._runner is not None and not self._runner.done()
        return {
            "healthy": runner_running,
            "worker_token": self.worker_token,
            "runner_running": runner_running,
            "stopping": self._stop_event.is_set(),
            "inflight_tasks": len(self._inflight),
        }

    def _on_runner_done(self, task: asyncio.Task) -> None:
        """Log unexpected worker termination so health checks have context."""
        with suppress(asyncio.CancelledError):
            exc = task.exception()
            if exc is not None:
                logger.exception("Analysis task worker stopped unexpectedly: %s", exc)


async def recover_stuck_tasks() -> int:
    """
    Requeue stale active tasks so the background worker can retry them.

    Returns the number of requeued tasks.
    """
    now = datetime.now(timezone.utc)
    requeued = await requeue_stale_tasks(
        queued_before=now - QUEUED_STALE_AFTER,
        active_before=now - ACTIVE_STALE_AFTER,
    )
    if requeued:
        logger.info("Requeued %d stale analysis tasks", requeued)
    return requeued


async def _heartbeat_loop(task_id: UUID, worker_token: str) -> None:
    """Keep the claimed task fresh while this worker is actively processing it."""
    while True:
        await asyncio.sleep(TASK_HEARTBEAT_INTERVAL_SECONDS)
        await touch_task_heartbeat(task_id, worker_token)
