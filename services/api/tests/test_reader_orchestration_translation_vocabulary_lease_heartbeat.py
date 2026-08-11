from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.contracts.annotation import compute_text_range_hash, utf16_code_unit_length
from app.schemas.reader_orchestration import (
    TranslationBatchGenerationOutput,
    TranslationBatchGroupOutput,
    TranslationBatchUnitOutput,
    TranslationGenerationGroup,
    TranslationLayerGenerationOutput,
    VocabularyLayerOutput,
)
from app.services.reader_orchestration import (
    translation_worker as translation_worker_module,
)
from app.services.reader_orchestration import vocabulary_worker as vocabulary_worker_module
from app.services.reader_orchestration.job_bootstrap import (
    TRANSLATION_BATCH_OPERATION_FINGERPRINT,
    TRANSLATION_OPERATION_FINGERPRINT,
    VOCABULARY_OPERATION_FINGERPRINT,
)
from app.services.reader_orchestration.job_runtime import (
    ClaimResult,
    IllegalTransitionError,
)
from app.services.reader_orchestration.lease_heartbeat import LeaseHeartbeat
from app.services.reader_orchestration.reading_strategy import (
    resolve_reader_variant_strategy,
)
from app.services.reader_orchestration.translation_worker import (
    TranslationAnchorSegmentTarget,
    TranslationBatchExecutionResult,
    TranslationBatchJobContext,
    TranslationBatchUnitContext,
    TranslationExecutionResult,
    TranslationJobContext,
    TranslationWorkerService,
    build_deterministic_translation_groups,
)
from app.services.reader_orchestration.vocabulary_worker import (
    VocabularyBatchCandidateOutput,
    VocabularyBatchExecutionResult,
    VocabularyBatchJobContext,
    VocabularyBatchUnitCandidateOutput,
    VocabularyBatchUnitContext,
    VocabularyExecutionResult,
    VocabularyJobContext,
    VocabularyWorkerService,
)

pytestmark = pytest.mark.anyio

_LEASE_DURATION = timedelta(milliseconds=80)
_HEARTBEAT_INTERVAL = timedelta(milliseconds=10)
_WAIT_TIMEOUT_SECONDS = 0.25


class _FastLeaseHeartbeat(LeaseHeartbeat):
    def __init__(self, **kwargs: Any) -> None:
        kwargs["lease_duration"] = _LEASE_DURATION
        kwargs["heartbeat_interval"] = _HEARTBEAT_INTERVAL
        super().__init__(**kwargs)


class _DelayedProvider:
    def __init__(self, output_factory: Callable[[Any], Any]) -> None:
        self._output_factory = output_factory
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.running = False
        self.attempts = 0

    async def _execute(self, context: Any) -> Any:
        self.attempts += 1
        self.running = True
        self.started.set()
        try:
            await self.release.wait()
        finally:
            self.running = False
        return self._output_factory(context)

    translate = _execute
    translate_batch = _execute
    generate = _execute
    generate_batch = _execute


class _LoseAfterOneRenewalRuntime:
    def __init__(self, provider: _DelayedProvider) -> None:
        self._provider = provider
        self.heartbeat_calls = 0
        self.renewed_while_provider_running = False
        self.ownership_lost = asyncio.Event()
        self.transitions: list[dict[str, Any]] = []

    async def heartbeat(
        self,
        *,
        job_id: UUID,
        lease_token: UUID,
        lease_duration: timedelta,
    ) -> datetime:
        self.heartbeat_calls += 1
        if self.heartbeat_calls == 1:
            self.renewed_while_provider_running = self._provider.running
            return datetime.now(UTC) + lease_duration
        self.ownership_lost.set()
        raise IllegalTransitionError("claim ownership lost")

    async def transition(self, **kwargs: Any) -> SimpleNamespace:
        self.transitions.append(kwargs)
        return SimpleNamespace(status=kwargs["target_status"])


class _Journal:
    async def begin_execution(self, **_: Any) -> SimpleNamespace:
        return SimpleNamespace(provider_call_allowed=True, capture_state="started")


class _Publisher:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def _publish(self, **_: Any) -> object:
        self.events.append("publish")
        return object()

    publish_unit_translation = _publish
    publish_article_translation_batch = _publish
    publish_unit_vocabulary = _publish
    publish_article_vocabulary_batch = _publish


def _claim(*, batch: bool) -> ClaimResult:
    return ClaimResult(
        job_id=uuid4(),
        run_id=uuid4(),
        reading_record_id=uuid4(),
        user_id=uuid4(),
        base_id=uuid4(),
        job_type="batch" if batch else "unit",
        target_type="article" if batch else "unit",
        target_key="article" if batch else "u1",
        expected_generation=1,
        operation_fingerprint="heartbeat-contract",
        attempt_count=1,
        lease_owner="heartbeat-contract",
        lease_token=uuid4(),
        lease_expires_at=datetime.now(UTC) + _LEASE_DURATION,
    )


def _translation_context(claim: ClaimResult, *, batch: bool) -> object:
    source_text = "Translation source text."
    strategy = resolve_reader_variant_strategy("daily_reading", "intermediate_reading")
    layer = strategy.layers["translation"]
    segment = TranslationAnchorSegmentTarget(
        anchor_segment_id="s1",
        sentence_id="s1",
        order_index=1,
        segment_type="sentence",
        boundary_quality="normal",
        unit_start_utf16=0,
        unit_end_utf16=utf16_code_unit_length(source_text),
        text_hash=compute_text_range_hash(source_text),
        source_text=source_text,
    )
    common = dict(
        job_id=claim.job_id,
        run_id=claim.run_id,
        reading_record_id=claim.reading_record_id,
        user_id=claim.user_id,
        base_id=claim.base_id,
        expected_generation=claim.expected_generation,
        source_language="en",
        reading_goal=strategy.reading_goal,
        reading_variant=strategy.reading_variant,
        strategy_version=strategy.strategy_version,
        strategy_hash=strategy.strategy_hash,
        layer_policy_hash=layer.policy_hash,
        translation_prompt_lines=layer.prompt_lines,
    )
    if batch:
        unit = TranslationBatchUnitContext(
            unit_id="u1",
            order_index=1,
            source_text=source_text,
            text_hash=compute_text_range_hash(source_text),
            anchor_segments=(segment,),
        )
        return TranslationBatchJobContext(
            **common,
            operation_fingerprint=TRANSLATION_BATCH_OPERATION_FINGERPRINT,
            target_language="zh-CN",
            target_unit_ids=(unit.unit_id,),
            units=(unit,),
        )
    return TranslationJobContext(
        **common,
        unit_id="u1",
        order_index=1,
        operation_fingerprint=TRANSLATION_OPERATION_FINGERPRINT,
        target_language="zh-CN",
        source_text=source_text,
        text_hash=compute_text_range_hash(source_text),
        anchor_segments=(segment,),
    )


def _vocabulary_context(claim: ClaimResult, *, batch: bool) -> object:
    source_text = "Vocabulary source text."
    strategy = resolve_reader_variant_strategy("daily_reading", "intermediate_reading")
    layer = strategy.layers["vocabulary"]
    common = dict(
        job_id=claim.job_id,
        run_id=claim.run_id,
        reading_record_id=claim.reading_record_id,
        user_id=claim.user_id,
        base_id=claim.base_id,
        expected_generation=claim.expected_generation,
        operation_fingerprint=VOCABULARY_OPERATION_FINGERPRINT,
        source_language="en",
        reading_goal=strategy.reading_goal,
        reading_variant=strategy.reading_variant,
        strategy_version=strategy.strategy_version,
        strategy_hash=strategy.strategy_hash,
        layer_policy_hash=layer.policy_hash,
        vocabulary_prompt_lines=layer.prompt_lines,
    )
    if batch:
        unit = VocabularyBatchUnitContext(
            unit_id="u1",
            order_index=1,
            source_text=source_text,
            text_hash=compute_text_range_hash(source_text),
            anchor_segments=(),
        )
        return VocabularyBatchJobContext(
            **common,
            target_unit_ids=(unit.unit_id,),
            units=(unit,),
        )
    return VocabularyJobContext(
        **common,
        unit_id="u1",
        order_index=1,
        source_text=source_text,
        text_hash=compute_text_range_hash(source_text),
        anchor_segments=(),
    )


def _translation_unit_execution(_: TranslationJobContext) -> TranslationExecutionResult:
    return TranslationExecutionResult(
        output=TranslationLayerGenerationOutput(
            groups=[
                TranslationGenerationGroup(
                    anchor_segment_ids=["s1"],
                    translated_text="译文。",
                )
            ]
        )
    )


def _translation_batch_execution(
    context: TranslationBatchJobContext,
) -> TranslationBatchExecutionResult:
    groups = tuple(
        group
        for unit in context.units
        for group in build_deterministic_translation_groups(unit)
    )
    return TranslationBatchExecutionResult(
        output=TranslationBatchGenerationOutput(
            units=[
                TranslationBatchUnitOutput(
                    unit_id=unit_id,
                    groups=[
                        TranslationBatchGroupOutput(
                            group_id=group.group_id,
                            translated_text="译文。",
                        )
                        for group in groups
                        if group.unit_id == unit_id
                    ],
                )
                for unit_id in context.target_unit_ids
            ]
        )
    )


def _vocabulary_unit_execution(_: VocabularyJobContext) -> VocabularyExecutionResult:
    return VocabularyExecutionResult(output=VocabularyLayerOutput(items=[]))


def _vocabulary_batch_execution(
    context: VocabularyBatchJobContext,
) -> VocabularyBatchExecutionResult:
    return VocabularyBatchExecutionResult(
        output=VocabularyBatchCandidateOutput(
            units=[
                VocabularyBatchUnitCandidateOutput(unit_id=unit_id, items=[])
                for unit_id in context.target_unit_ids
            ]
        )
    )


async def _noop_span(**_: Any) -> None:
    return None


def _lingering_heartbeat_tasks() -> list[asyncio.Task[Any]]:
    return [
        task
        for task in asyncio.all_tasks()
        if not task.done() and task.get_name().startswith("lease-heartbeat-")
    ]


async def _exercise_contract(
    *,
    monkeypatch: pytest.MonkeyPatch,
    module: Any,
    service: Any,
    provider: _DelayedProvider,
    runtime: _LoseAfterOneRenewalRuntime,
    claim: ClaimResult,
    context: object,
    load_name: str,
    capture_name: str,
    process_name: str,
    events: list[str],
) -> None:
    async def capture(**_: Any) -> UUID:
        events.append("capture")
        return uuid4()

    async def reconcile(**kwargs: Any) -> UUID:
        return kwargs["fallback_event_id"]

    async def load(_: UUID) -> object:
        return context

    monkeypatch.setattr(module, "LeaseHeartbeat", _FastLeaseHeartbeat, raising=False)
    monkeypatch.setattr(module, "end_worker_span_success", _noop_span)
    monkeypatch.setattr(service, load_name, load)
    monkeypatch.setattr(service, capture_name, capture)
    monkeypatch.setattr(service, "_reconcile_captured_usage", reconcile)

    process_task = asyncio.create_task(getattr(service, process_name)(claim=claim))
    await asyncio.wait_for(provider.started.wait(), timeout=_WAIT_TIMEOUT_SECONDS)
    try:
        await asyncio.wait_for(
            runtime.ownership_lost.wait(), timeout=_WAIT_TIMEOUT_SECONDS
        )
        loss_observed = True
    except TimeoutError:
        loss_observed = False
    finally:
        provider.release.set()
    result = await asyncio.wait_for(process_task, timeout=_WAIT_TIMEOUT_SECONDS)

    assert {
        "loss_observed": loss_observed,
        "renewed_while_provider_running": runtime.renewed_while_provider_running,
        "status": result.status,
        "events": events,
        "transitions": runtime.transitions,
        "provider_attempts": provider.attempts,
        "heartbeat_tasks": _lingering_heartbeat_tasks(),
    } == {
        "loss_observed": True,
        "renewed_while_provider_running": True,
        "status": "retry_later",
        "events": ["capture"],
        "transitions": [],
        "provider_attempts": 1,
        "heartbeat_tasks": [],
    }


@pytest.mark.anyio
async def test_translation_unit_renews_lease_and_skips_publish_after_ownership_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = _claim(batch=False)
    provider = _DelayedProvider(_translation_unit_execution)
    runtime = _LoseAfterOneRenewalRuntime(provider)
    events: list[str] = []
    service = TranslationWorkerService(
        job_runtime=runtime,
        layer_publisher=_Publisher(events),
        translator=provider,
        batch_translator=provider,
        journal_service=_Journal(),
    )
    await _exercise_contract(
        monkeypatch=monkeypatch,
        module=translation_worker_module,
        service=service,
        provider=provider,
        runtime=runtime,
        claim=claim,
        context=_translation_context(claim, batch=False),
        load_name="_load_job_context",
        capture_name="_capture_translation_execution",
        process_name="process_claimed_translation_job",
        events=events,
    )


@pytest.mark.anyio
async def test_translation_batch_renews_lease_and_skips_publish_after_ownership_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = _claim(batch=True)
    provider = _DelayedProvider(_translation_batch_execution)
    runtime = _LoseAfterOneRenewalRuntime(provider)
    events: list[str] = []
    service = TranslationWorkerService(
        job_runtime=runtime,
        layer_publisher=_Publisher(events),
        translator=provider,
        batch_translator=provider,
        journal_service=_Journal(),
    )
    await _exercise_contract(
        monkeypatch=monkeypatch,
        module=translation_worker_module,
        service=service,
        provider=provider,
        runtime=runtime,
        claim=claim,
        context=_translation_context(claim, batch=True),
        load_name="_load_batch_job_context",
        capture_name="_capture_translation_batch_execution",
        process_name="process_claimed_translation_batch_job",
        events=events,
    )


@pytest.mark.anyio
async def test_vocabulary_unit_renews_lease_and_skips_publish_after_ownership_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = _claim(batch=False)
    provider = _DelayedProvider(_vocabulary_unit_execution)
    runtime = _LoseAfterOneRenewalRuntime(provider)
    events: list[str] = []
    service = VocabularyWorkerService(
        job_runtime=runtime,
        layer_publisher=_Publisher(events),
        executor=provider,
        batch_executor=provider,
        journal_service=_Journal(),
    )
    await _exercise_contract(
        monkeypatch=monkeypatch,
        module=vocabulary_worker_module,
        service=service,
        provider=provider,
        runtime=runtime,
        claim=claim,
        context=_vocabulary_context(claim, batch=False),
        load_name="_load_job_context",
        capture_name="_capture_vocabulary_execution",
        process_name="process_claimed_vocabulary_job",
        events=events,
    )


@pytest.mark.anyio
async def test_vocabulary_batch_renews_lease_and_skips_publish_after_ownership_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = _claim(batch=True)
    provider = _DelayedProvider(_vocabulary_batch_execution)
    runtime = _LoseAfterOneRenewalRuntime(provider)
    events: list[str] = []
    service = VocabularyWorkerService(
        job_runtime=runtime,
        layer_publisher=_Publisher(events),
        executor=provider,
        batch_executor=provider,
        journal_service=_Journal(),
    )
    await _exercise_contract(
        monkeypatch=monkeypatch,
        module=vocabulary_worker_module,
        service=service,
        provider=provider,
        runtime=runtime,
        claim=claim,
        context=_vocabulary_context(claim, batch=True),
        load_name="_load_batch_job_context",
        capture_name="_capture_vocabulary_batch_execution",
        process_name="process_claimed_vocabulary_batch_job",
        events=events,
    )
