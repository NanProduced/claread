from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4

from langsmith import traceable

from app.agents.learning_overview_hint_agent import (
    LearningOverviewHintAgentDeps,
    build_learning_overview_hint_prompt,
    get_learning_overview_hint_agent,
)
from app.config.settings import get_settings
from app.llm.agent_runner import extract_run_usage, run_agent_with_route
from app.llm.routes import MODEL_ROUTE_ANNOTATION_GENERATION
from app.schemas.internal.overview_hint import StoredOverviewHint
from app.services.ai_usage import (
    AIUsageEventCreate,
    CAPABILITY_ANALYSIS_OVERVIEW_HINT,
    BILLING_MODE_INTERNAL_ONLY,
    STATUS_FAILED,
    STATUS_SUCCEEDED,
    USAGE_SCOPE_SYSTEM_INTERNAL,
    record_ai_usage_event,
    resolve_model_metadata,
)
from app.services.analysis.overview_task_service import (
    OverviewTaskExecutionPayload,
    claim_next_queued_task,
    insert_task_event,
    requeue_stale_tasks,
    touch_task_heartbeat,
    update_record_overview_hint,
    update_task_status,
)
from app.observability.workflow_tracing import build_llm_trace_metadata, build_workflow_root_metadata

logger = logging.getLogger(__name__)

WORKFLOW_NAME = "analysis_overview_hint"
WORKFLOW_VERSION = "1.0.0"
SCHEMA_VERSION = "overview-hint-v1"

QUEUED_STALE_AFTER = timedelta(minutes=5)
ACTIVE_STALE_AFTER = timedelta(minutes=5)
MAX_CONCURRENT_TASKS = 4
CLAIM_POLL_INTERVAL_SECONDS = 1.0
SHUTDOWN_WAIT_SECONDS = 5.0
TASK_HEARTBEAT_INTERVAL_SECONDS = 30.0


def _collect_sentence_texts(render_scene_json: dict[str, Any]) -> list[str]:
    article = render_scene_json.get("article")
    sentences = article.get("sentences") if isinstance(article, dict) else None
    if not isinstance(sentences, list):
        return []
    items: list[str] = []
    for sentence in sentences:
        if not isinstance(sentence, dict):
            continue
        text = str(sentence.get("text") or "").strip()
        if text:
            items.append(text)
    return items


def _collect_translations(render_scene_json: dict[str, Any]) -> list[str]:
    translations = render_scene_json.get("translations")
    if not isinstance(translations, list):
        return []
    items: list[str] = []
    for item in translations[:6]:
        if not isinstance(item, dict):
            continue
        text = str(item.get("translation_zh") or item.get("translationZh") or "").strip()
        if text:
            items.append(text)
    return items


def _collect_sentence_entries(render_scene_json: dict[str, Any]) -> list[dict[str, str]]:
    entries = render_scene_json.get("sentence_entries") or render_scene_json.get("sentenceEntries")
    if not isinstance(entries, list):
        return []
    normalized: list[dict[str, str]] = []
    for entry in entries[:6]:
        if not isinstance(entry, dict):
            continue
        normalized.append(
            {
                "entry_type": str(entry.get("entry_type") or entry.get("entryType") or "").strip(),
                "label": str(entry.get("label") or "").strip(),
                "title": str(entry.get("title") or "").strip(),
                "content": str(entry.get("content") or "").strip(),
            }
        )
    return normalized


def _build_trace_metadata(
    *,
    payload: OverviewTaskExecutionPayload,
    model_name: str,
    model_provider: str,
) -> dict[str, object]:
    return build_llm_trace_metadata(
        workflow_name=WORKFLOW_NAME,
        workflow_version=WORKFLOW_VERSION,
        request_id=str(payload.task_id),
        source_type="analysis_record",
        reading_goal=payload.reading_goal,
        reading_variant=payload.reading_variant,
        profile_id="learning_overview_hint",
        model_name=model_name,
        model_provider=model_provider,
        surface="overview_worker",
        extra={
            "record_id": str(payload.record_id),
            "source_hash": payload.source_text_hash,
            "derivative_kind": "overview_hint",
        },
    )


@traceable(name="learning_overview_hint_llm_call", run_type="llm")
async def _run_learning_overview_hint_llm_span(
    *,
    deps: LearningOverviewHintAgentDeps,
    payload: OverviewTaskExecutionPayload,
    metadata: dict[str, object],
    langsmith_extra: dict[str, object] | None = None,
) -> dict[str, Any]:
    result = await run_agent_with_route(
        agent=get_learning_overview_hint_agent(),
        prompt=build_learning_overview_hint_prompt(deps),
        deps=deps,
        route=MODEL_ROUTE_ANNOTATION_GENERATION,
        model_selection=None,
    )
    usage = extract_run_usage(result)
    resolved_model = getattr(result, "_resolved_model_config", None)
    output = result.output if hasattr(result, "output") else result
    return {
        "output": output,
        "usage_metadata": usage,
        "trace_metadata": _build_trace_metadata(
            payload=payload,
            model_name=getattr(resolved_model, "model_name", "unknown"),
            model_provider=getattr(resolved_model, "provider", "unknown"),
        ),
        "resolved_model": resolved_model,
    }


async def _execute_task_impl(
    payload: OverviewTaskExecutionPayload,
) -> None:
    heartbeat_task: asyncio.Task | None = None
    usage_summary: dict[str, Any] | None = None
    request_id = str(payload.task_id)
    model_metadata = resolve_model_metadata(get_settings(), MODEL_ROUTE_ANNOTATION_GENERATION)
    started_at = datetime.now(timezone.utc)
    started_perf = perf_counter()

    try:
        await update_task_status(
            payload.task_id,
            status="running",
            started_at=started_at,
            worker_token=payload.worker_token,
        )
        await insert_task_event(
            payload.task_id,
            "task_started",
            {"worker_token": payload.worker_token},
        )
        heartbeat_task = asyncio.create_task(
            _heartbeat_loop(payload.task_id, payload.worker_token),
            name=f"overview-task-heartbeat-{payload.task_id}",
        )

        deps = LearningOverviewHintAgentDeps(
            source_text=payload.text,
            reading_goal=payload.reading_goal,
            reading_variant=payload.reading_variant,
            sentence_texts=_collect_sentence_texts(payload.render_scene_json),
            translations=_collect_translations(payload.render_scene_json),
            sentence_entries=_collect_sentence_entries(payload.render_scene_json),
        )
        trace_metadata = _build_trace_metadata(
            payload=payload,
            model_name=model_metadata.get("model_name", "unknown"),
            model_provider=model_metadata.get("model_provider", "unknown"),
        )
        result = await _run_learning_overview_hint_llm_span(
            deps=deps,
            payload=payload,
            metadata=trace_metadata,
            langsmith_extra={"metadata": trace_metadata},
        )
        output = result["output"]
        usage_summary = result.get("usage_metadata")

        await update_task_status(payload.task_id, status="finalizing")
        await insert_task_event(payload.task_id, "task_finalizing", {"status": output.status})

        hint = StoredOverviewHint(
            status=output.status,
            overview=getattr(output, "overview", None),
            confidence=getattr(output, "confidence", None),
            reason=getattr(output, "reason", None),
            source="learning_overview_hint_agent",
            source_text_hash=payload.source_text_hash,
            workflow_version=payload.workflow_version,
            schema_version=payload.schema_version,
            updated_at=datetime.now(timezone.utc).isoformat(),
            task_id=str(payload.task_id),
        )
        await update_record_overview_hint(record_id=payload.record_id, hint=hint)

        await record_ai_usage_event(
            AIUsageEventCreate(
                usage_scope=USAGE_SCOPE_SYSTEM_INTERNAL,
                capability_code=CAPABILITY_ANALYSIS_OVERVIEW_HINT,
                billing_mode=BILLING_MODE_INTERNAL_ONLY,
                status=STATUS_SUCCEEDED,
                user_id=payload.user_id,
                task_id=None,
                record_id=payload.record_id,
                request_id=request_id,
                workflow_name=WORKFLOW_NAME,
                workflow_version=WORKFLOW_VERSION,
                schema_version=SCHEMA_VERSION,
                prompt_version="overview-hint-agent",
                usage_data=usage_summary,
                latency_ms=int((perf_counter() - started_perf) * 1000),
                billed_points=0,
                metadata_json={
                    "entrypoint": "overview-task-worker",
                    "task_execution_mode": "worker",
                    "derivative_kind": "overview_hint",
                    "overview_status": output.status,
                },
                **model_metadata,
            )
        )

        await update_task_status(
            payload.task_id,
            status="succeeded",
            finished_at=datetime.now(timezone.utc),
            usage_summary_json=usage_summary or {},
        )
        await insert_task_event(
            payload.task_id,
            "task_succeeded",
            {"overview_status": output.status, "usage_summary": usage_summary or {}},
        )
    except Exception as exc:
        logger.exception("Overview task %s failed: %s", payload.task_id, exc)
        failure_code = type(exc).__name__
        failure_message = str(exc)[:500]
        hint = StoredOverviewHint(
            status="failed",
            reason=failure_message,
            source="learning_overview_hint_agent",
            source_text_hash=payload.source_text_hash,
            workflow_version=payload.workflow_version,
            schema_version=payload.schema_version,
            updated_at=datetime.now(timezone.utc).isoformat(),
            task_id=str(payload.task_id),
        )
        with suppress(Exception):
            await update_record_overview_hint(record_id=payload.record_id, hint=hint)
        with suppress(Exception):
            await update_task_status(
                payload.task_id,
                status="failed",
                finished_at=datetime.now(timezone.utc),
                failure_code=failure_code,
                failure_message=failure_message,
                usage_summary_json=usage_summary or {},
            )
        with suppress(Exception):
            await insert_task_event(
                payload.task_id,
                "task_failed",
                {
                    "failure_code": failure_code,
                    "failure_message": failure_message,
                    "usage_summary": usage_summary or {},
                },
            )
        with suppress(Exception):
            await record_ai_usage_event(
                AIUsageEventCreate(
                    usage_scope=USAGE_SCOPE_SYSTEM_INTERNAL,
                    capability_code=CAPABILITY_ANALYSIS_OVERVIEW_HINT,
                    billing_mode=BILLING_MODE_INTERNAL_ONLY,
                    status=STATUS_FAILED,
                    user_id=payload.user_id,
                    task_id=None,
                    record_id=payload.record_id,
                    request_id=request_id,
                    workflow_name=WORKFLOW_NAME,
                    workflow_version=WORKFLOW_VERSION,
                    schema_version=SCHEMA_VERSION,
                    prompt_version="overview-hint-agent",
                    usage_data=usage_summary,
                    latency_ms=int((perf_counter() - started_perf) * 1000),
                    billed_points=0,
                    error_code=failure_code,
                    error_message=failure_message,
                    metadata_json={
                        "entrypoint": "overview-task-worker",
                        "task_execution_mode": "worker",
                        "derivative_kind": "overview_hint",
                    },
                    **model_metadata,
                )
            )
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task


@traceable(name=WORKFLOW_NAME, run_type="chain")
async def execute_task(
    payload: OverviewTaskExecutionPayload,
    *,
    langsmith_extra: dict[str, object] | None = None,
) -> None:
    await _execute_task_impl(payload)


def launch_task(payload: OverviewTaskExecutionPayload) -> asyncio.Task:
    metadata = build_workflow_root_metadata(
        workflow_name=WORKFLOW_NAME,
        workflow_version=WORKFLOW_VERSION,
        schema_version=SCHEMA_VERSION,
        request_id=str(payload.task_id),
        source_type="analysis_record",
        reading_goal=payload.reading_goal,
        reading_variant=payload.reading_variant,
        profile_id="learning_overview_hint",
        surface="overview_worker",
        extra={
            "record_id": str(payload.record_id),
            "source_hash": payload.source_text_hash,
            "derivative_kind": "overview_hint",
        },
    )
    return asyncio.create_task(
        execute_task(
            payload,
            langsmith_extra={"metadata": metadata},
        ),
        name=f"overview-task-{payload.task_id}",
    )


class OverviewTaskWorker:
    def __init__(
        self,
        *,
        max_concurrency: int = MAX_CONCURRENT_TASKS,
        poll_interval_seconds: float = CLAIM_POLL_INTERVAL_SECONDS,
    ) -> None:
        self.max_concurrency = max_concurrency
        self.poll_interval_seconds = poll_interval_seconds
        self.worker_token = f"overview-worker-{uuid4()}"
        self._runner: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._inflight: set[asyncio.Task] = set()

    def start(self) -> asyncio.Task:
        if self._runner is None:
            self._runner = asyncio.create_task(
                self.run_forever(),
                name="overview-task-worker",
            )
            self._runner.add_done_callback(self._on_runner_done)
        return self._runner

    async def stop(self) -> None:
        self._stop_event.set()
        if self._runner is not None:
            await self._runner
            self._runner = None
        if self._inflight:
            await asyncio.wait(self._inflight, timeout=SHUTDOWN_WAIT_SECONDS)

    async def run_forever(self) -> None:
        while not self._stop_event.is_set():
            claimed_any = False
            while not self._stop_event.is_set() and len(self._inflight) < self.max_concurrency:
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
        runner_running = self._runner is not None and not self._runner.done()
        return {
            "healthy": runner_running,
            "worker_token": self.worker_token,
            "runner_running": runner_running,
            "stopping": self._stop_event.is_set(),
            "inflight_tasks": len(self._inflight),
        }

    def _on_runner_done(self, task: asyncio.Task) -> None:
        with suppress(asyncio.CancelledError):
            exc = task.exception()
            if exc is not None:
                logger.exception("Overview task worker stopped unexpectedly: %s", exc)


async def recover_stuck_tasks() -> int:
    now = datetime.now(timezone.utc)
    requeued = await requeue_stale_tasks(
        queued_before=now - QUEUED_STALE_AFTER,
        active_before=now - ACTIVE_STALE_AFTER,
    )
    if requeued:
        logger.info("Requeued %d stale overview tasks", requeued)
    return requeued


async def _heartbeat_loop(task_id: UUID, worker_token: str) -> None:
    while True:
        await asyncio.sleep(TASK_HEARTBEAT_INTERVAL_SECONDS)
        await touch_task_heartbeat(task_id, worker_token)
