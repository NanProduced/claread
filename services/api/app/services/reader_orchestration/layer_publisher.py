from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypeVar
from uuid import UUID, uuid4

import asyncpg
from pydantic import ValidationError

from app.contracts.annotation import compute_text_range_hash, slice_by_utf16_offsets
from app.database import connection as db_connection
from app.database.json_compat import jsonb_param
from app.schemas.reader_orchestration import (
    GrammarBundleOutput,
    GrammarNoteLayerOutput,
    ReaderTextRangeAnchor,
    SentenceAnalysisLayerOutput,
    TranslationLayerOutput,
    VocabularyLayerOutput,
)

from .event_runtime import ReaderEventEnvelope, ReaderEventRuntime
from .grammar_layer_payload import build_grammar_layer_published_payload
from .grammar_layer_payload_validator import (
    validate_grammar_layer_published_payload,
)
from .job_bootstrap import (
    GRAMMAR_BATCH_JOB_TYPE,
    GRAMMAR_BATCH_OPERATION_FINGERPRINT,
    GRAMMAR_BATCH_TARGET_SCOPE,
    GRAMMAR_JOB_TYPE,
    GRAMMAR_OPERATION_FINGERPRINT,
    GRAMMAR_STRUCTURED_BATCH_OPERATION_FINGERPRINT,
    GRAMMAR_TARGET_SCOPE,
    TRANSLATION_BATCH_JOB_TYPE,
    TRANSLATION_BATCH_OPERATION_FINGERPRINT,
    TRANSLATION_BATCH_TARGET_SCOPE,
    TRANSLATION_OPERATION_FINGERPRINT,
    TRANSLATION_STRUCTURED_BATCH_OPERATION_FINGERPRINT,
    VOCABULARY_BATCH_JOB_TYPE,
    VOCABULARY_BATCH_OPERATION_FINGERPRINT,
    VOCABULARY_BATCH_TARGET_SCOPE,
    VOCABULARY_JOB_TYPE,
    VOCABULARY_OPERATION_FINGERPRINT,
    VOCABULARY_STRUCTURED_BATCH_OPERATION_FINGERPRINT,
    VOCABULARY_TARGET_SCOPE,
    _fingerprint_matches_base,
)
from .job_runtime import (
    FenceViolationError,
    ReaderJobRuntime,
    _assert_lease_valid,
)
from .repository import ReaderOrchestrationRepository
from .span_recorder import (
    SPAN_KIND_PUBLISH_FENCE,
    STATUS_FAILED,
    STATUS_SUCCEEDED,
    current_span,
    get_default_recorder,
)
from .translation_parsed_decision import (
    TRANSLATION_PARSED_POLICY_CODE,
    TRANSLATION_PARSED_RATIONALE_CODE,
    build_translation_parsed_decision_documents,
    build_translation_parsed_decision_event_payload,
)

_T = TypeVar("_T")

GRAMMAR_NOTE_LAYER_OPERATION_FINGERPRINT = "grammar_note_unit_v1"
SENTENCE_ANALYSIS_LAYER_OPERATION_FINGERPRINT = "sentence_analysis_unit_v1"
TRANSLATION_LAYER_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class PublishedTranslationLayer:
    layer_id: UUID
    reading_record_id: UUID
    base_id: UUID
    unit_id: str
    generation: int
    event: ReaderEventEnvelope


@dataclass(frozen=True, slots=True)
class PublishedVocabularyLayer:
    layer_id: UUID
    reading_record_id: UUID
    base_id: UUID
    unit_id: str
    generation: int
    event: ReaderEventEnvelope


@dataclass(frozen=True, slots=True)
class PublishedGrammarLayer:
    layer_id: UUID
    layer_type: str
    reading_record_id: UUID
    base_id: UUID
    unit_id: str
    generation: int
    event: ReaderEventEnvelope


@dataclass(frozen=True, slots=True)
class PublishedGrammarBundle:
    reading_record_id: UUID
    base_id: UUID
    unit_id: str
    generation: int
    grammar_note_layer: PublishedGrammarLayer | None
    sentence_analysis_layer: PublishedGrammarLayer | None
    events: tuple[ReaderEventEnvelope, ...]
    no_op: bool = False


@dataclass(frozen=True, slots=True)
class PublishedGrammarBatch:
    """T4.1c compact grammar batch publish result.

    One batch job produces N per-unit ``enhancement_layers`` rows
    (``grammar_note`` and/or ``sentence_analysis`` per unit).
    ``layer_ids`` / ``layer_types`` preserve the publish order for
    observability and AI usage recording.
    """

    reading_record_id: UUID
    base_id: UUID
    generation: int
    layers: tuple[PublishedGrammarLayer, ...]
    layer_ids: tuple[str, ...]
    layer_types: tuple[str, ...]
    no_op: bool = False


@dataclass(frozen=True, slots=True)
class PublishedTranslationBatch:
    """T1.1 short-article batch publish result.

    One batch job produces N per-unit ``enhancement_layers`` rows (one per
    unit covered by the batch). ``layers`` preserves the unit order from
    the batch output.
    """

    reading_record_id: UUID
    base_id: UUID
    generation: int
    layers: tuple[PublishedTranslationLayer, ...]


@dataclass(frozen=True, slots=True)
class PublishedVocabularyBatch:
    """T1.1 short-article batch publish result for the vocabulary layer."""

    reading_record_id: UUID
    base_id: UUID
    generation: int
    layers: tuple[PublishedVocabularyLayer, ...]


@dataclass(frozen=True, slots=True)
class _UnitAnchorValidationContext:
    unit_id: str
    unit_text: str
    unit_text_hash: str
    ordered_segments: tuple[asyncpg.Record, ...]
    segments_by_id: dict[str, asyncpg.Record]


@dataclass(frozen=True, slots=True)
class _ValidatedTranslationGroup:
    group_id: str
    anchor_segment_ids: tuple[str, ...]
    source_text_hash: str
    translated_text_length: int


@dataclass(frozen=True, slots=True)
class _ValidatedTranslationOutput:
    output: TranslationLayerOutput
    covered_anchor_segment_ids: tuple[str, ...]
    missing_anchor_segment_ids: tuple[str, ...]
    groups: tuple[_ValidatedTranslationGroup, ...]


class TranslationPublishValidationError(ValueError):
    def __init__(self, failure_code: str, message: str) -> None:
        super().__init__(message)
        self.failure_code = failure_code


class TranslationLayerPublisher:
    def __init__(
        self,
        *,
        pool: asyncpg.Pool | None = None,
        job_runtime: ReaderJobRuntime | None = None,
        event_runtime: ReaderEventRuntime | None = None,
        repository: ReaderOrchestrationRepository | None = None,
    ) -> None:
        self._pool = pool
        self._job_runtime = job_runtime or ReaderJobRuntime(pool=pool)
        self._event_runtime = event_runtime or ReaderEventRuntime(pool=pool)
        self._repository = repository or ReaderOrchestrationRepository(pool=pool)

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    async def publish_unit_translation(
        self,
        *,
        job_id: UUID,
        lease_token: UUID,
        output: TranslationLayerOutput,
        quality_json: dict[str, Any] | None = None,
    ) -> PublishedTranslationLayer:
        """Wrap the publish fence + DB write in a ``publish_fence`` span.

        Captures fence violations, validation errors, and DB write failures
        as span ``status='failed'`` with ``failure_class`` /
        ``failure_code``. ``reading_record_id`` is left NULL at span start
        (PG column is NULLable); the row is queryable via ``trace_id``.
        """

        parent = current_span()
        recorder = get_default_recorder()
        publish_span = await recorder.start_span(
            trace_id=parent.trace_id if parent is not None else uuid4(),
            span_kind=SPAN_KIND_PUBLISH_FENCE,
            parent_span_id=parent.span_id if parent is not None else None,
            reader_job_id=job_id,
            metadata={"layer_type": "translation"},
        )
        try:
            result = await self._publish_unit_translation_inner(
                job_id=job_id,
                lease_token=lease_token,
                output=output,
                quality_json=quality_json,
            )
            await recorder.end_span(
                publish_span,
                status=STATUS_SUCCEEDED,
                extra_metadata={
                    "layer_id": str(result.layer_id),
                    "unit_id": result.unit_id,
                    "generation": result.generation,
                },
            )
            return result
        except FenceViolationError:
            await recorder.end_span(
                publish_span,
                status=STATUS_FAILED,
                failure_class="fence_violation",
                failure_code="fence_failed",
            )
            raise
        except Exception as exc:
            await recorder.end_span(
                publish_span,
                status=STATUS_FAILED,
                failure_class="publish_exception",
                failure_code=type(exc).__name__,
            )
            raise

    async def _publish_unit_translation_inner(
        self,
        *,
        job_id: UUID,
        lease_token: UUID,
        output: TranslationLayerOutput,
        quality_json: dict[str, Any] | None = None,
    ) -> PublishedTranslationLayer:
        published_at = datetime.now(UTC)

        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                job_row = await conn.fetchrow(
                    "SELECT * FROM reader_jobs WHERE id = $1 FOR UPDATE",
                    job_id,
                )
                if job_row is None:
                    raise LookupError(f"reader job {job_id} not found")
                if job_row["status"] != "claimed":
                    raise ValueError("translation publish requires a claimed job")
                if job_row["job_type"] != "translate_unit" or job_row["target_type"] != "unit":
                    raise ValueError("translation publish requires a translate_unit/unit job")

                _assert_lease_valid(job_row, job_id, lease_token)
                fence_error = await self._job_runtime._validate_fence(conn, job_row)  # type: ignore[attr-defined]
                if fence_error is not None:
                    raise FenceViolationError(
                        f"publish fence failed for job {job_id}: {fence_error}"
                    )

                base_id = job_row["base_id"]
                if base_id is None:
                    raise FenceViolationError(
                        f"publish fence failed for job {job_id}: missing_base"
                    )
                reading_record_id = job_row["reading_record_id"]
                generation = int(job_row["expected_generation"])
                unit_id = str(job_row["target_key"])

                validation = await _validate_translation_output_for_publish(
                    conn,
                    reading_record_id=reading_record_id,
                    base_id=base_id,
                    unit_id=unit_id,
                    operation_fingerprint=str(job_row["operation_fingerprint"] or ""),
                    output=output,
                )
                await _assert_no_published_layer(
                    conn,
                    reading_record_id=reading_record_id,
                    base_id=base_id,
                    unit_id=unit_id,
                    generation=generation,
                    layer_type="translation",
                )

                payload = validation.output.model_dump(mode="json")
                coverage_summary = _build_translation_coverage_summary(
                    unit_id=unit_id,
                    generation=generation,
                    validation=validation,
                )
                layer_id = uuid4()
                coverage_json, decision_json = build_translation_parsed_decision_documents(
                    layer_id=layer_id,
                    coverage_summary=coverage_summary,
                )
                effective_quality_json = dict(quality_json or {})
                effective_quality_json["group_count"] = len(validation.groups)
                effective_quality_json["covered_anchor_segment_count"] = len(
                    validation.covered_anchor_segment_ids
                )

                layer_row = await conn.fetchrow(
                    """
                    INSERT INTO enhancement_layers (
                        id,
                        reading_record_id,
                        base_id,
                        layer_type,
                        layer_subtype,
                        target_scope,
                        target_key,
                        generation,
                        status,
                        operation_fingerprint,
                        schema_version,
                        output_json,
                        coverage_json,
                        quality_json,
                        source_run_id,
                        source_job_id,
                        published_at
                    )
                    VALUES (
                        $1,
                        $2,
                        $3,
                        'translation',
                        NULL,
                        'unit',
                        $4,
                        $5,
                        'published',
                        $6,
                        $7,
                        $8::jsonb,
                        $9::jsonb,
                        $10::jsonb,
                        $11,
                        $12,
                        $13
                    )
                    RETURNING id
                    """,
                    layer_id,
                    reading_record_id,
                    base_id,
                    unit_id,
                    generation,
                    job_row["operation_fingerprint"],
                    TRANSLATION_LAYER_SCHEMA_VERSION,
                    jsonb_param(payload),
                    jsonb_param(coverage_json),
                    jsonb_param(effective_quality_json),
                    job_row["run_id"],
                    job_id,
                    published_at,
                )
                if layer_row is None:
                    raise RuntimeError("enhancement_layers insert did not return a row")

                event = await self._event_runtime.publish_event_in_transaction(
                    conn,
                    record_id=reading_record_id,
                    event_type="layer_published",
                    payload_json={
                        "record_id": str(reading_record_id),
                        "base_id": str(base_id),
                        "layer_id": str(layer_row["id"]),
                        "layer_type": "translation",
                        "target_scope": "unit",
                        "target_key": unit_id,
                        "generation": generation,
                    },
                    source_run_id=job_row["run_id"],
                    source_job_id=job_id,
                    source_layer_id=layer_row["id"],
                    created_at=published_at,
                )
                await self._repository.upsert_parsed_decision(
                    conn,
                    reading_record_id=reading_record_id,
                    base_id=base_id,
                    unit_id=unit_id,
                    policy_code=TRANSLATION_PARSED_POLICY_CODE,
                    parsed_state="parsed",
                    rationale_code=TRANSLATION_PARSED_RATIONALE_CODE,
                    coverage_json=coverage_json,
                    source_layer_id=layer_row["id"],
                    source_job_id=job_id,
                    decision_json=decision_json,
                )
                await self._event_runtime.publish_event_in_transaction(
                    conn,
                    record_id=reading_record_id,
                    event_type="parsed_decision_updated",
                    payload_json=build_translation_parsed_decision_event_payload(
                        reading_record_id=reading_record_id,
                        base_id=base_id,
                        unit_id=unit_id,
                        source_layer_id=layer_row["id"],
                        source_job_id=job_id,
                    ),
                    source_run_id=job_row["run_id"],
                    source_job_id=job_id,
                    source_layer_id=layer_row["id"],
                    created_at=published_at,
                )

                updated_job = await self._job_runtime._apply_transition(  # type: ignore[attr-defined]
                    conn,
                    job_row=job_row,
                    target_status="succeeded",
                    available_at=None,
                    pause_owner=None,
                    output_ref={
                        "layer_id": str(layer_row["id"]),
                        "event_id": str(event.event_id),
                    },
                    failure_class=None,
                    failure_code=None,
                    failure_message=None,
                    rationale_code="translation_published",
                )
                await self._job_runtime._insert_job_event(  # type: ignore[attr-defined]
                    conn,
                    reading_record_id=updated_job["reading_record_id"],
                    run_id=updated_job["run_id"],
                    job_id=updated_job["id"],
                    event_type="job_succeeded",
                    payload={
                        "previous_status": job_row["status"],
                        "target_status": "succeeded",
                        "rationale_code": "translation_published",
                    },
                )
                await conn.execute(
                    """
                    UPDATE reader_runs
                    SET status = 'completed',
                        failure_class = NULL,
                        failure_code = NULL,
                        finished_at = $2,
                        updated_at = $2
                    WHERE id = $1
                    """,
                    job_row["run_id"],
                    published_at,
                )

                return PublishedTranslationLayer(
                    layer_id=layer_row["id"],
                    reading_record_id=reading_record_id,
                    base_id=base_id,
                    unit_id=unit_id,
                    generation=generation,
                    event=event,
                )

    async def publish_article_translation_batch(
        self,
        *,
        job_id: UUID,
        lease_token: UUID,
        outputs: list[tuple[str, TranslationLayerOutput]],
        quality_json: dict[str, Any] | None = None,
    ) -> PublishedTranslationBatch:
        """T1.1 short-article batch publish: wrap fence + N per-unit writes in
        a ``publish_fence`` span.

        ``outputs`` is a list of ``(unit_id, TranslationLayerOutput)`` pairs
        produced by the batch worker. The publisher validates each per-unit
        output against the existing anchor validation contract, inserts one
        ``enhancement_layers`` row per unit (``target_scope='unit'``), and
        transitions the single batch job → ``succeeded``.
        """

        parent = current_span()
        recorder = get_default_recorder()
        publish_span = await recorder.start_span(
            trace_id=parent.trace_id if parent is not None else uuid4(),
            span_kind=SPAN_KIND_PUBLISH_FENCE,
            parent_span_id=parent.span_id if parent is not None else None,
            reader_job_id=job_id,
            metadata={"layer_type": "translation", "batch": True},
        )
        try:
            result = await self._publish_article_translation_batch_inner(
                job_id=job_id,
                lease_token=lease_token,
                outputs=outputs,
                quality_json=quality_json,
            )
            await recorder.end_span(
                publish_span,
                status=STATUS_SUCCEEDED,
                extra_metadata={
                    "unit_count": len(result.layers),
                    "generation": result.generation,
                    "layer_ids": [str(layer.layer_id) for layer in result.layers],
                },
            )
            return result
        except FenceViolationError:
            await recorder.end_span(
                publish_span,
                status=STATUS_FAILED,
                failure_class="fence_violation",
                failure_code="fence_failed",
            )
            raise
        except Exception as exc:
            await recorder.end_span(
                publish_span,
                status=STATUS_FAILED,
                failure_class="publish_exception",
                failure_code=type(exc).__name__,
            )
            raise

    async def _publish_article_translation_batch_inner(
        self,
        *,
        job_id: UUID,
        lease_token: UUID,
        outputs: list[tuple[str, TranslationLayerOutput]],
        quality_json: dict[str, Any] | None = None,
    ) -> PublishedTranslationBatch:
        published_at = datetime.now(UTC)

        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                job_row = await conn.fetchrow(
                    "SELECT * FROM reader_jobs WHERE id = $1 FOR UPDATE",
                    job_id,
                )
                if job_row is None:
                    raise LookupError(f"reader job {job_id} not found")
                if job_row["status"] != "claimed":
                    raise ValueError(
                        "translation batch publish requires a claimed job"
                    )
                if (
                    job_row["job_type"] != TRANSLATION_BATCH_JOB_TYPE
                    or job_row["target_type"] != TRANSLATION_BATCH_TARGET_SCOPE
                ):
                    raise ValueError(
                        "translation batch publish requires a "
                        "translate_article/unit_range job"
                    )

                _assert_lease_valid(job_row, job_id, lease_token)
                fence_error = await self._job_runtime._validate_fence(conn, job_row)  # type: ignore[attr-defined]
                if fence_error is not None:
                    raise FenceViolationError(
                        f"publish fence failed for job {job_id}: {fence_error}"
                    )

                base_id = job_row["base_id"]
                if base_id is None:
                    raise FenceViolationError(
                        f"publish fence failed for job {job_id}: missing_base"
                    )
                reading_record_id = job_row["reading_record_id"]
                generation = int(job_row["expected_generation"])
                operation_fingerprint = str(job_row["operation_fingerprint"] or "")

                if not (
                    _fingerprint_matches_base(
                        operation_fingerprint,
                        TRANSLATION_BATCH_OPERATION_FINGERPRINT,
                    )
                    or _fingerprint_matches_base(
                        operation_fingerprint,
                        TRANSLATION_STRUCTURED_BATCH_OPERATION_FINGERPRINT,
                    )
                ):
                    raise TranslationPublishValidationError(
                        "translation_batch_fingerprint_mismatch",
                        (
                            f"translation batch publish fingerprint "
                            f"{operation_fingerprint!r} does not match either "
                            f"{TRANSLATION_BATCH_OPERATION_FINGERPRINT!r} or "
                            f"{TRANSLATION_STRUCTURED_BATCH_OPERATION_FINGERPRINT!r}"
                        ),
                    )

                # Fail-closed: the batch output must cover exactly the units
                # listed in the job input_json ``target_unit_ids``. Any
                # mismatch (missing unit, extra unit, duplicate unit) fails
                # the entire batch job before any layer is written.
                input_json = job_row["input_json"]
                target_unit_ids: list[str] = list(input_json.get("target_unit_ids") or [])
                output_unit_ids = [unit_id for unit_id, _ in outputs]
                if sorted(target_unit_ids) != sorted(output_unit_ids):
                    raise TranslationPublishValidationError(
                        "translation_batch_unit_id_mismatch",
                        (
                            f"translation batch output unit_ids "
                            f"{output_unit_ids!r} do not match target_unit_ids "
                            f"{target_unit_ids!r}"
                        ),
                    )
                seen_unit_ids: set[str] = set()
                for unit_id, _ in outputs:
                    if unit_id in seen_unit_ids:
                        raise TranslationPublishValidationError(
                            "translation_batch_duplicate_unit_id",
                            f"translation batch output has duplicate unit_id {unit_id!r}",
                        )
                    seen_unit_ids.add(unit_id)

                # T1 acceptance: reorder outputs to match target_unit_ids
                # (reading order) so published layers/events appear in the
                # order the reader reads them, regardless of the order the
                # batch executor returned.
                outputs = _reorder_outputs_by_target_unit_ids(
                    outputs, target_unit_ids
                )

                published_layers: list[PublishedTranslationLayer] = []
                layer_ids: list[str] = []
                event_ids: list[str] = []
                for unit_id, unit_output in outputs:
                    validation = await _validate_translation_unit_output_core(
                        conn,
                        reading_record_id=reading_record_id,
                        base_id=base_id,
                        unit_id=unit_id,
                        parsed_output=unit_output,
                    )
                    await _assert_no_published_layer(
                        conn,
                        reading_record_id=reading_record_id,
                        base_id=base_id,
                        unit_id=unit_id,
                        generation=generation,
                        layer_type="translation",
                    )

                    payload = validation.output.model_dump(mode="json")
                    coverage_summary = _build_translation_coverage_summary(
                        unit_id=unit_id,
                        generation=generation,
                        validation=validation,
                    )
                    layer_id = uuid4()
                    coverage_json, decision_json = (
                        build_translation_parsed_decision_documents(
                            layer_id=layer_id,
                            coverage_summary=coverage_summary,
                        )
                    )
                    effective_quality_json = dict(quality_json or {})
                    effective_quality_json["group_count"] = len(validation.groups)
                    effective_quality_json["covered_anchor_segment_count"] = len(
                        validation.covered_anchor_segment_ids
                    )
                    effective_quality_json["batch"] = True

                    # T1.1: append ``:unit_id`` to the per-unit layer
                    # fingerprint so the ``uq_enhancement_layers_source_job_fingerprint``
                    # unique constraint ``(source_job_id, operation_fingerprint)``
                    # is not violated when N per-unit layers are published from
                    # one batch job. The batch job's own fingerprint is
                    # preserved on ``reader_jobs.operation_fingerprint``; the
                    # per-unit layer's fingerprint is only used for
                    # traceability and the unique constraint.
                    unit_layer_fingerprint = (
                        f"{job_row['operation_fingerprint']}:{unit_id}"
                    )

                    layer_row = await conn.fetchrow(
                        """
                        INSERT INTO enhancement_layers (
                            id,
                            reading_record_id,
                            base_id,
                            layer_type,
                            layer_subtype,
                            target_scope,
                            target_key,
                            generation,
                            status,
                            operation_fingerprint,
                            schema_version,
                            output_json,
                            coverage_json,
                            quality_json,
                            source_run_id,
                            source_job_id,
                            published_at
                        )
                        VALUES (
                            $1,
                            $2,
                            $3,
                            'translation',
                            NULL,
                            'unit',
                            $4,
                            $5,
                            'published',
                            $6,
                            $7,
                            $8::jsonb,
                            $9::jsonb,
                            $10::jsonb,
                            $11,
                            $12,
                            $13
                        )
                        RETURNING id
                        """,
                        layer_id,
                        reading_record_id,
                        base_id,
                        unit_id,
                        generation,
                        unit_layer_fingerprint,
                        TRANSLATION_LAYER_SCHEMA_VERSION,
                        jsonb_param(payload),
                        jsonb_param(coverage_json),
                        jsonb_param(effective_quality_json),
                        job_row["run_id"],
                        job_id,
                        published_at,
                    )
                    if layer_row is None:
                        raise RuntimeError(
                            "enhancement_layers insert did not return a row"
                        )

                    event = await self._event_runtime.publish_event_in_transaction(
                        conn,
                        record_id=reading_record_id,
                        event_type="layer_published",
                        payload_json={
                            "record_id": str(reading_record_id),
                            "base_id": str(base_id),
                            "layer_id": str(layer_row["id"]),
                            "layer_type": "translation",
                            "target_scope": "unit",
                            "target_key": unit_id,
                            "generation": generation,
                        },
                        source_run_id=job_row["run_id"],
                        source_job_id=job_id,
                        source_layer_id=layer_row["id"],
                        created_at=published_at,
                    )
                    await self._repository.upsert_parsed_decision(
                        conn,
                        reading_record_id=reading_record_id,
                        base_id=base_id,
                        unit_id=unit_id,
                        policy_code=TRANSLATION_PARSED_POLICY_CODE,
                        parsed_state="parsed",
                        rationale_code=TRANSLATION_PARSED_RATIONALE_CODE,
                        coverage_json=coverage_json,
                        source_layer_id=layer_row["id"],
                        source_job_id=job_id,
                        decision_json=decision_json,
                    )
                    await self._event_runtime.publish_event_in_transaction(
                        conn,
                        record_id=reading_record_id,
                        event_type="parsed_decision_updated",
                        payload_json=build_translation_parsed_decision_event_payload(
                            reading_record_id=reading_record_id,
                            base_id=base_id,
                            unit_id=unit_id,
                            source_layer_id=layer_row["id"],
                            source_job_id=job_id,
                        ),
                        source_run_id=job_row["run_id"],
                        source_job_id=job_id,
                        source_layer_id=layer_row["id"],
                        created_at=published_at,
                    )

                    published_layers.append(
                        PublishedTranslationLayer(
                            layer_id=layer_row["id"],
                            reading_record_id=reading_record_id,
                            base_id=base_id,
                            unit_id=unit_id,
                            generation=generation,
                            event=event,
                        )
                    )
                    layer_ids.append(str(layer_row["id"]))
                    event_ids.append(str(event.event_id))

                updated_job = await self._job_runtime._apply_transition(  # type: ignore[attr-defined]
                    conn,
                    job_row=job_row,
                    target_status="succeeded",
                    available_at=None,
                    pause_owner=None,
                    output_ref={
                        "layer_ids": layer_ids,
                        "event_ids": event_ids,
                        "unit_count": len(published_layers),
                    },
                    failure_class=None,
                    failure_code=None,
                    failure_message=None,
                    rationale_code="translation_batch_published",
                )
                await self._job_runtime._insert_job_event(  # type: ignore[attr-defined]
                    conn,
                    reading_record_id=updated_job["reading_record_id"],
                    run_id=updated_job["run_id"],
                    job_id=updated_job["id"],
                    event_type="job_succeeded",
                    payload={
                        "previous_status": job_row["status"],
                        "target_status": "succeeded",
                        "rationale_code": "translation_batch_published",
                    },
                )
                await conn.execute(
                    """
                    UPDATE reader_runs
                    SET status = 'completed',
                        failure_class = NULL,
                        failure_code = NULL,
                        finished_at = $2,
                        updated_at = $2
                    WHERE id = $1
                    """,
                    job_row["run_id"],
                    published_at,
                )

                return PublishedTranslationBatch(
                    reading_record_id=reading_record_id,
                    base_id=base_id,
                    generation=generation,
                    layers=tuple(published_layers),
                )


class VocabularyLayerPublisher:
    def __init__(
        self,
        *,
        pool: asyncpg.Pool | None = None,
        job_runtime: ReaderJobRuntime | None = None,
        event_runtime: ReaderEventRuntime | None = None,
    ) -> None:
        self._pool = pool
        self._job_runtime = job_runtime or ReaderJobRuntime(pool=pool)
        self._event_runtime = event_runtime or ReaderEventRuntime(pool=pool)

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    async def publish_unit_vocabulary(
        self,
        *,
        job_id: UUID,
        lease_token: UUID,
        output: VocabularyLayerOutput,
        quality_json: dict[str, Any] | None = None,
    ) -> PublishedVocabularyLayer:
        """Wrap the publish fence + DB write in a ``publish_fence`` span."""

        parent = current_span()
        recorder = get_default_recorder()
        publish_span = await recorder.start_span(
            trace_id=parent.trace_id if parent is not None else uuid4(),
            span_kind=SPAN_KIND_PUBLISH_FENCE,
            parent_span_id=parent.span_id if parent is not None else None,
            reader_job_id=job_id,
            metadata={"layer_type": "vocabulary"},
        )
        try:
            result = await self._publish_unit_vocabulary_inner(
                job_id=job_id,
                lease_token=lease_token,
                output=output,
                quality_json=quality_json,
            )
            await recorder.end_span(
                publish_span,
                status=STATUS_SUCCEEDED,
                extra_metadata={
                    "layer_id": str(result.layer_id),
                    "unit_id": result.unit_id,
                    "generation": result.generation,
                },
            )
            return result
        except FenceViolationError:
            await recorder.end_span(
                publish_span,
                status=STATUS_FAILED,
                failure_class="fence_violation",
                failure_code="fence_failed",
            )
            raise
        except Exception as exc:
            await recorder.end_span(
                publish_span,
                status=STATUS_FAILED,
                failure_class="publish_exception",
                failure_code=type(exc).__name__,
            )
            raise

    async def _publish_unit_vocabulary_inner(
        self,
        *,
        job_id: UUID,
        lease_token: UUID,
        output: VocabularyLayerOutput,
        quality_json: dict[str, Any] | None = None,
    ) -> PublishedVocabularyLayer:
        payload = output.model_dump(mode="json")
        published_at = datetime.now(UTC)

        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                job_row = await conn.fetchrow(
                    "SELECT * FROM reader_jobs WHERE id = $1 FOR UPDATE",
                    job_id,
                )
                if job_row is None:
                    raise LookupError(f"reader job {job_id} not found")
                if job_row["status"] != "claimed":
                    raise ValueError("vocabulary publish requires a claimed job")
                if (
                    job_row["job_type"] != VOCABULARY_JOB_TYPE
                    or job_row["target_type"] != VOCABULARY_TARGET_SCOPE
                    or not _fingerprint_matches_base(
                        job_row["operation_fingerprint"],
                        VOCABULARY_OPERATION_FINGERPRINT,
                    )
                ):
                    raise ValueError(
                        "vocabulary publish requires a build_vocabulary_layer/unit job"
                    )

                _assert_lease_valid(job_row, job_id, lease_token)
                fence_error = await self._job_runtime._validate_fence(conn, job_row)  # type: ignore[attr-defined]
                if fence_error is not None:
                    raise FenceViolationError(
                        f"publish fence failed for job {job_id}: {fence_error}"
                    )

                base_id = job_row["base_id"]
                if base_id is None:
                    raise FenceViolationError(
                        f"publish fence failed for job {job_id}: missing_base"
                    )
                reading_record_id = job_row["reading_record_id"]
                generation = int(job_row["expected_generation"])
                unit_id = str(job_row["target_key"])

                unit_row = await conn.fetchrow(
                    """
                    SELECT unit_id
                    FROM reading_units
                    WHERE reading_record_id = $1
                      AND base_id = $2
                      AND unit_id = $3
                    """,
                    reading_record_id,
                    base_id,
                    unit_id,
                )
                if unit_row is None:
                    raise ValueError(f"vocabulary publish target unit {unit_id} does not exist")

                existing_layer = await conn.fetchrow(
                    """
                    SELECT id
                    FROM enhancement_layers
                    WHERE reading_record_id = $1
                      AND base_id = $2
                      AND layer_type = 'vocabulary'
                      AND target_scope = 'unit'
                      AND target_key = $3
                      AND generation = $4
                      AND status = 'published'
                    LIMIT 1
                    """,
                    reading_record_id,
                    base_id,
                    unit_id,
                    generation,
                )
                if existing_layer is not None:
                    raise ValueError(f"vocabulary layer already published for unit {unit_id}")

                await _validate_vocabulary_items(
                    conn,
                    reading_record_id=reading_record_id,
                    base_id=base_id,
                    unit_id=unit_id,
                    output=output,
                )

                layer_row = await conn.fetchrow(
                    """
                    INSERT INTO enhancement_layers (
                        reading_record_id,
                        base_id,
                        layer_type,
                        layer_subtype,
                        target_scope,
                        target_key,
                        generation,
                        status,
                        operation_fingerprint,
                        schema_version,
                        output_json,
                        coverage_json,
                        quality_json,
                        source_run_id,
                        source_job_id,
                        published_at
                    )
                    VALUES (
                        $1,
                        $2,
                        'vocabulary',
                        NULL,
                        'unit',
                        $3,
                        $4,
                        'published',
                        $5,
                        $6,
                        $7::jsonb,
                        '{}'::jsonb,
                        $8::jsonb,
                        $9,
                        $10,
                        $11
                    )
                    RETURNING id
                    """,
                    reading_record_id,
                    base_id,
                    unit_id,
                    generation,
                    job_row["operation_fingerprint"],
                    int(output.schema_version),
                    jsonb_param(payload),
                    jsonb_param(dict(quality_json or {})),
                    job_row["run_id"],
                    job_id,
                    published_at,
                )
                if layer_row is None:
                    raise RuntimeError("enhancement_layers insert did not return a row")

                event = await self._event_runtime.publish_event_in_transaction(
                    conn,
                    record_id=reading_record_id,
                    event_type="layer_published",
                    payload_json={
                        "record_id": str(reading_record_id),
                        "base_id": str(base_id),
                        "layer_id": str(layer_row["id"]),
                        "layer_type": "vocabulary",
                        "target_scope": "unit",
                        "target_key": unit_id,
                        "generation": generation,
                    },
                    source_run_id=job_row["run_id"],
                    source_job_id=job_id,
                    source_layer_id=layer_row["id"],
                    created_at=published_at,
                )

                updated_job = await self._job_runtime._apply_transition(  # type: ignore[attr-defined]
                    conn,
                    job_row=job_row,
                    target_status="succeeded",
                    available_at=None,
                    pause_owner=None,
                    output_ref={
                        "layer_id": str(layer_row["id"]),
                        "event_id": str(event.event_id),
                    },
                    failure_class=None,
                    failure_code=None,
                    failure_message=None,
                    rationale_code="vocabulary_published",
                )
                await self._job_runtime._insert_job_event(  # type: ignore[attr-defined]
                    conn,
                    reading_record_id=updated_job["reading_record_id"],
                    run_id=updated_job["run_id"],
                    job_id=updated_job["id"],
                    event_type="job_succeeded",
                    payload={
                        "previous_status": job_row["status"],
                        "target_status": "succeeded",
                        "rationale_code": "vocabulary_published",
                    },
                )
                await conn.execute(
                    """
                    UPDATE reader_runs
                    SET status = 'completed',
                        failure_class = NULL,
                        failure_code = NULL,
                        finished_at = $2,
                        updated_at = $2
                    WHERE id = $1
                    """,
                    job_row["run_id"],
                    published_at,
                )

                return PublishedVocabularyLayer(
                    layer_id=layer_row["id"],
                    reading_record_id=reading_record_id,
                    base_id=base_id,
                    unit_id=unit_id,
                    generation=generation,
                    event=event,
                )

    async def publish_article_vocabulary_batch(
        self,
        *,
        job_id: UUID,
        lease_token: UUID,
        outputs: list[tuple[str, VocabularyLayerOutput]],
        quality_json: dict[str, Any] | None = None,
    ) -> PublishedVocabularyBatch:
        """T1.1 short-article batch publish for the vocabulary layer.

        Mirrors :meth:`TranslationLayerPublisher.publish_article_translation_batch`
        but without the parsed-decision upsert (vocabulary has no
        parsed_decision contract).
        """

        parent = current_span()
        recorder = get_default_recorder()
        publish_span = await recorder.start_span(
            trace_id=parent.trace_id if parent is not None else uuid4(),
            span_kind=SPAN_KIND_PUBLISH_FENCE,
            parent_span_id=parent.span_id if parent is not None else None,
            reader_job_id=job_id,
            metadata={"layer_type": "vocabulary", "batch": True},
        )
        try:
            result = await self._publish_article_vocabulary_batch_inner(
                job_id=job_id,
                lease_token=lease_token,
                outputs=outputs,
                quality_json=quality_json,
            )
            await recorder.end_span(
                publish_span,
                status=STATUS_SUCCEEDED,
                extra_metadata={
                    "unit_count": len(result.layers),
                    "generation": result.generation,
                    "layer_ids": [str(layer.layer_id) for layer in result.layers],
                },
            )
            return result
        except FenceViolationError:
            await recorder.end_span(
                publish_span,
                status=STATUS_FAILED,
                failure_class="fence_violation",
                failure_code="fence_failed",
            )
            raise
        except Exception as exc:
            await recorder.end_span(
                publish_span,
                status=STATUS_FAILED,
                failure_class="publish_exception",
                failure_code=type(exc).__name__,
            )
            raise

    async def _publish_article_vocabulary_batch_inner(
        self,
        *,
        job_id: UUID,
        lease_token: UUID,
        outputs: list[tuple[str, VocabularyLayerOutput]],
        quality_json: dict[str, Any] | None = None,
    ) -> PublishedVocabularyBatch:
        published_at = datetime.now(UTC)

        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                job_row = await conn.fetchrow(
                    "SELECT * FROM reader_jobs WHERE id = $1 FOR UPDATE",
                    job_id,
                )
                if job_row is None:
                    raise LookupError(f"reader job {job_id} not found")
                if job_row["status"] != "claimed":
                    raise ValueError(
                        "vocabulary batch publish requires a claimed job"
                    )
                if (
                    job_row["job_type"] != VOCABULARY_BATCH_JOB_TYPE
                    or job_row["target_type"] != VOCABULARY_BATCH_TARGET_SCOPE
                ):
                    raise ValueError(
                        "vocabulary batch publish requires a "
                        "build_vocabulary_layer_article/unit_range job"
                    )

                _assert_lease_valid(job_row, job_id, lease_token)
                fence_error = await self._job_runtime._validate_fence(conn, job_row)  # type: ignore[attr-defined]
                if fence_error is not None:
                    raise FenceViolationError(
                        f"publish fence failed for job {job_id}: {fence_error}"
                    )

                base_id = job_row["base_id"]
                if base_id is None:
                    raise FenceViolationError(
                        f"publish fence failed for job {job_id}: missing_base"
                    )
                reading_record_id = job_row["reading_record_id"]
                generation = int(job_row["expected_generation"])
                operation_fingerprint = str(job_row["operation_fingerprint"] or "")

                if not (
                    _fingerprint_matches_base(
                        operation_fingerprint,
                        VOCABULARY_BATCH_OPERATION_FINGERPRINT,
                    )
                    or _fingerprint_matches_base(
                        operation_fingerprint,
                        VOCABULARY_STRUCTURED_BATCH_OPERATION_FINGERPRINT,
                    )
                ):
                    raise ValueError(
                        f"vocabulary batch publish fingerprint "
                        f"{operation_fingerprint!r} does not match either "
                        f"{VOCABULARY_BATCH_OPERATION_FINGERPRINT!r} or "
                        f"{VOCABULARY_STRUCTURED_BATCH_OPERATION_FINGERPRINT!r}"
                    )

                input_json = job_row["input_json"]
                target_unit_ids: list[str] = list(input_json.get("target_unit_ids") or [])
                output_unit_ids = [unit_id for unit_id, _ in outputs]
                if sorted(target_unit_ids) != sorted(output_unit_ids):
                    raise ValueError(
                        f"vocabulary batch output unit_ids {output_unit_ids!r} "
                        f"do not match target_unit_ids {target_unit_ids!r}"
                    )
                seen_unit_ids: set[str] = set()
                for unit_id, _ in outputs:
                    if unit_id in seen_unit_ids:
                        raise ValueError(
                            f"vocabulary batch output has duplicate unit_id {unit_id!r}"
                        )
                    seen_unit_ids.add(unit_id)

                # T1 acceptance: reorder outputs to match target_unit_ids
                # (reading order) so published layers/events appear in the
                # order the reader reads them, regardless of the order the
                # batch executor returned.
                outputs = _reorder_outputs_by_target_unit_ids(
                    outputs, target_unit_ids
                )

                published_layers: list[PublishedVocabularyLayer] = []
                layer_ids: list[str] = []
                event_ids: list[str] = []
                for unit_id, unit_output in outputs:
                    unit_row = await conn.fetchrow(
                        """
                        SELECT unit_id
                        FROM reading_units
                        WHERE reading_record_id = $1
                          AND base_id = $2
                          AND unit_id = $3
                        """,
                        reading_record_id,
                        base_id,
                        unit_id,
                    )
                    if unit_row is None:
                        raise ValueError(
                            f"vocabulary batch publish target unit {unit_id} does not exist"
                        )

                    existing_layer = await conn.fetchrow(
                        """
                        SELECT id
                        FROM enhancement_layers
                        WHERE reading_record_id = $1
                          AND base_id = $2
                          AND layer_type = 'vocabulary'
                          AND target_scope = 'unit'
                          AND target_key = $3
                          AND generation = $4
                          AND status = 'published'
                        LIMIT 1
                        """,
                        reading_record_id,
                        base_id,
                        unit_id,
                        generation,
                    )
                    if existing_layer is not None:
                        raise ValueError(
                            f"vocabulary layer already published for unit {unit_id}"
                        )

                    await _validate_vocabulary_items(
                        conn,
                        reading_record_id=reading_record_id,
                        base_id=base_id,
                        unit_id=unit_id,
                        output=unit_output,
                    )

                    payload = unit_output.model_dump(mode="json")
                    effective_quality_json = dict(quality_json or {})
                    effective_quality_json["batch"] = True

                    # T1.1: append ``:unit_id`` to the per-unit layer
                    # fingerprint so the ``uq_enhancement_layers_source_job_fingerprint``
                    # unique constraint ``(source_job_id, operation_fingerprint)``
                    # is not violated when N per-unit layers are published from
                    # one batch job. See ``publish_article_translation_batch``
                    # for the same pattern.
                    unit_layer_fingerprint = (
                        f"{job_row['operation_fingerprint']}:{unit_id}"
                    )

                    layer_row = await conn.fetchrow(
                        """
                        INSERT INTO enhancement_layers (
                            reading_record_id,
                            base_id,
                            layer_type,
                            layer_subtype,
                            target_scope,
                            target_key,
                            generation,
                            status,
                            operation_fingerprint,
                            schema_version,
                            output_json,
                            coverage_json,
                            quality_json,
                            source_run_id,
                            source_job_id,
                            published_at
                        )
                        VALUES (
                            $1,
                            $2,
                            'vocabulary',
                            NULL,
                            'unit',
                            $3,
                            $4,
                            'published',
                            $5,
                            $6,
                            $7::jsonb,
                            '{}'::jsonb,
                            $8::jsonb,
                            $9,
                            $10,
                            $11
                        )
                        RETURNING id
                        """,
                        reading_record_id,
                        base_id,
                        unit_id,
                        generation,
                        unit_layer_fingerprint,
                        int(unit_output.schema_version),
                        jsonb_param(payload),
                        jsonb_param(effective_quality_json),
                        job_row["run_id"],
                        job_id,
                        published_at,
                    )
                    if layer_row is None:
                        raise RuntimeError(
                            "enhancement_layers insert did not return a row"
                        )

                    event = await self._event_runtime.publish_event_in_transaction(
                        conn,
                        record_id=reading_record_id,
                        event_type="layer_published",
                        payload_json={
                            "record_id": str(reading_record_id),
                            "base_id": str(base_id),
                            "layer_id": str(layer_row["id"]),
                            "layer_type": "vocabulary",
                            "target_scope": "unit",
                            "target_key": unit_id,
                            "generation": generation,
                        },
                        source_run_id=job_row["run_id"],
                        source_job_id=job_id,
                        source_layer_id=layer_row["id"],
                        created_at=published_at,
                    )

                    published_layers.append(
                        PublishedVocabularyLayer(
                            layer_id=layer_row["id"],
                            reading_record_id=reading_record_id,
                            base_id=base_id,
                            unit_id=unit_id,
                            generation=generation,
                            event=event,
                        )
                    )
                    layer_ids.append(str(layer_row["id"]))
                    event_ids.append(str(event.event_id))

                updated_job = await self._job_runtime._apply_transition(  # type: ignore[attr-defined]
                    conn,
                    job_row=job_row,
                    target_status="succeeded",
                    available_at=None,
                    pause_owner=None,
                    output_ref={
                        "layer_ids": layer_ids,
                        "event_ids": event_ids,
                        "unit_count": len(published_layers),
                    },
                    failure_class=None,
                    failure_code=None,
                    failure_message=None,
                    rationale_code="vocabulary_batch_published",
                )
                await self._job_runtime._insert_job_event(  # type: ignore[attr-defined]
                    conn,
                    reading_record_id=updated_job["reading_record_id"],
                    run_id=updated_job["run_id"],
                    job_id=updated_job["id"],
                    event_type="job_succeeded",
                    payload={
                        "previous_status": job_row["status"],
                        "target_status": "succeeded",
                        "rationale_code": "vocabulary_batch_published",
                    },
                )
                await conn.execute(
                    """
                    UPDATE reader_runs
                    SET status = 'completed',
                        failure_class = NULL,
                        failure_code = NULL,
                        finished_at = $2,
                        updated_at = $2
                    WHERE id = $1
                    """,
                    job_row["run_id"],
                    published_at,
                )

                return PublishedVocabularyBatch(
                    reading_record_id=reading_record_id,
                    base_id=base_id,
                    generation=generation,
                    layers=tuple(published_layers),
                )


class GrammarBundleLayerPublisher:
    def __init__(
        self,
        *,
        pool: asyncpg.Pool | None = None,
        job_runtime: ReaderJobRuntime | None = None,
        event_runtime: ReaderEventRuntime | None = None,
    ) -> None:
        self._pool = pool
        self._job_runtime = job_runtime or ReaderJobRuntime(pool=pool)
        self._event_runtime = event_runtime or ReaderEventRuntime(pool=pool)

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    async def publish_unit_grammar_bundle(
        self,
        *,
        job_id: UUID,
        lease_token: UUID,
        grammar_note_output: GrammarNoteLayerOutput | None,
        sentence_analysis_output: SentenceAnalysisLayerOutput | None,
        quality_json: dict[str, Any] | None = None,
    ) -> PublishedGrammarBundle:
        """Wrap the publish fence + DB write in a ``publish_fence`` span."""

        parent = current_span()
        recorder = get_default_recorder()
        publish_span = await recorder.start_span(
            trace_id=parent.trace_id if parent is not None else uuid4(),
            span_kind=SPAN_KIND_PUBLISH_FENCE,
            parent_span_id=parent.span_id if parent is not None else None,
            reader_job_id=job_id,
            metadata={"layer_type": "grammar_bundle"},
        )
        try:
            result = await self._publish_unit_grammar_bundle_inner(
                job_id=job_id,
                lease_token=lease_token,
                grammar_note_output=grammar_note_output,
                sentence_analysis_output=sentence_analysis_output,
                quality_json=quality_json,
            )
            await recorder.end_span(
                publish_span,
                status=STATUS_SUCCEEDED,
                extra_metadata={
                    "unit_id": result.unit_id,
                    "generation": result.generation,
                    "no_op": result.no_op,
                },
            )
            return result
        except FenceViolationError:
            await recorder.end_span(
                publish_span,
                status=STATUS_FAILED,
                failure_class="fence_violation",
                failure_code="fence_failed",
            )
            raise
        except Exception as exc:
            await recorder.end_span(
                publish_span,
                status=STATUS_FAILED,
                failure_class="publish_exception",
                failure_code=type(exc).__name__,
            )
            raise

    async def _publish_unit_grammar_bundle_inner(
        self,
        *,
        job_id: UUID,
        lease_token: UUID,
        grammar_note_output: GrammarNoteLayerOutput | None,
        sentence_analysis_output: SentenceAnalysisLayerOutput | None,
        quality_json: dict[str, Any] | None = None,
    ) -> PublishedGrammarBundle:
        quality_payload = dict(quality_json or {})
        published_at = datetime.now(UTC)

        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                job_row = await conn.fetchrow(
                    "SELECT * FROM reader_jobs WHERE id = $1 FOR UPDATE",
                    job_id,
                )
                if job_row is None:
                    raise LookupError(f"reader job {job_id} not found")
                if job_row["status"] != "claimed":
                    raise ValueError("grammar publish requires a claimed job")
                if (
                    job_row["job_type"] != GRAMMAR_JOB_TYPE
                    or job_row["target_type"] != GRAMMAR_TARGET_SCOPE
                    or not _fingerprint_matches_base(
                        job_row["operation_fingerprint"],
                        GRAMMAR_OPERATION_FINGERPRINT,
                    )
                ):
                    raise ValueError(
                        "grammar publish requires a build_grammar_bundle/unit job"
                    )

                _assert_lease_valid(job_row, job_id, lease_token)
                fence_error = await self._job_runtime._validate_fence(conn, job_row)  # type: ignore[attr-defined]
                if fence_error is not None:
                    raise FenceViolationError(
                        f"publish fence failed for job {job_id}: {fence_error}"
                    )

                base_id = job_row["base_id"]
                if base_id is None:
                    raise FenceViolationError(
                        f"publish fence failed for job {job_id}: missing_base"
                    )
                reading_record_id = job_row["reading_record_id"]
                generation = int(job_row["expected_generation"])
                unit_id = str(job_row["target_key"])

                unit_row = await conn.fetchrow(
                    """
                    SELECT unit_id
                    FROM reading_units
                    WHERE reading_record_id = $1
                      AND base_id = $2
                      AND unit_id = $3
                    """,
                    reading_record_id,
                    base_id,
                    unit_id,
                )
                if unit_row is None:
                    raise ValueError(f"grammar publish target unit {unit_id} does not exist")

                if grammar_note_output is not None:
                    await _assert_no_published_layer(
                        conn,
                        reading_record_id=reading_record_id,
                        base_id=base_id,
                        unit_id=unit_id,
                        generation=generation,
                        layer_type="grammar_note",
                    )
                    await _validate_grammar_note_items(
                        conn,
                        reading_record_id=reading_record_id,
                        base_id=base_id,
                        unit_id=unit_id,
                        output=grammar_note_output,
                    )
                if sentence_analysis_output is not None:
                    await _assert_no_published_layer(
                        conn,
                        reading_record_id=reading_record_id,
                        base_id=base_id,
                        unit_id=unit_id,
                        generation=generation,
                        layer_type="sentence_analysis",
                    )
                    await _validate_sentence_analysis_items(
                        conn,
                        reading_record_id=reading_record_id,
                        base_id=base_id,
                        unit_id=unit_id,
                        output=sentence_analysis_output,
                    )

                grammar_note_layer: PublishedGrammarLayer | None = None
                sentence_analysis_layer: PublishedGrammarLayer | None = None
                events: list[ReaderEventEnvelope] = []

                if grammar_note_output is not None:
                    grammar_note_layer = await _insert_published_grammar_layer(
                        conn,
                        event_runtime=self._event_runtime,
                        job_row=job_row,
                        job_id=job_id,
                        reading_record_id=reading_record_id,
                        base_id=base_id,
                        unit_id=unit_id,
                        generation=generation,
                        layer_type="grammar_note",
                        layer_operation_fingerprint=GRAMMAR_NOTE_LAYER_OPERATION_FINGERPRINT,
                        schema_version=int(grammar_note_output.schema_version),
                        payload=grammar_note_output.model_dump(mode="json"),
                        quality_json=quality_payload,
                        published_at=published_at,
                    )
                    events.append(grammar_note_layer.event)

                if sentence_analysis_output is not None:
                    sentence_analysis_layer = await _insert_published_grammar_layer(
                        conn,
                        event_runtime=self._event_runtime,
                        job_row=job_row,
                        job_id=job_id,
                        reading_record_id=reading_record_id,
                        base_id=base_id,
                        unit_id=unit_id,
                        generation=generation,
                        layer_type="sentence_analysis",
                        layer_operation_fingerprint=(
                            SENTENCE_ANALYSIS_LAYER_OPERATION_FINGERPRINT
                        ),
                        schema_version=int(sentence_analysis_output.schema_version),
                        payload=sentence_analysis_output.model_dump(mode="json"),
                        quality_json=quality_payload,
                        published_at=published_at,
                    )
                    events.append(sentence_analysis_layer.event)

                output_ref: dict[str, Any]
                rationale_code: str
                if grammar_note_layer is None and sentence_analysis_layer is None:
                    output_ref = {
                        "no_op": True,
                        "grammar_note_count": 0,
                        "sentence_analysis_count": 0,
                        "diagnostics": quality_payload.get("diagnostics", {}),
                    }
                    rationale_code = "grammar_bundle_no_op"
                else:
                    output_ref = {
                        "grammar_note_layer_id": (
                            str(grammar_note_layer.layer_id)
                            if grammar_note_layer is not None
                            else None
                        ),
                        "sentence_analysis_layer_id": (
                            str(sentence_analysis_layer.layer_id)
                            if sentence_analysis_layer is not None
                            else None
                        ),
                        "event_ids": [str(event.event_id) for event in events],
                        "no_op": False,
                    }
                    rationale_code = "grammar_bundle_published"

                updated_job = await self._job_runtime._apply_transition(  # type: ignore[attr-defined]
                    conn,
                    job_row=job_row,
                    target_status="succeeded",
                    available_at=None,
                    pause_owner=None,
                    output_ref=output_ref,
                    failure_class=None,
                    failure_code=None,
                    failure_message=None,
                    rationale_code=rationale_code,
                )
                await self._job_runtime._insert_job_event(  # type: ignore[attr-defined]
                    conn,
                    reading_record_id=updated_job["reading_record_id"],
                    run_id=updated_job["run_id"],
                    job_id=updated_job["id"],
                    event_type="job_succeeded",
                    payload={
                        "previous_status": job_row["status"],
                        "target_status": "succeeded",
                        "rationale_code": rationale_code,
                    },
                )
                await conn.execute(
                    """
                    UPDATE reader_runs
                    SET status = 'completed',
                        failure_class = NULL,
                        failure_code = NULL,
                        finished_at = $2,
                        updated_at = $2
                    WHERE id = $1
                    """,
                    job_row["run_id"],
                    published_at,
                )

                return PublishedGrammarBundle(
                    reading_record_id=reading_record_id,
                    base_id=base_id,
                    unit_id=unit_id,
                    generation=generation,
                    grammar_note_layer=grammar_note_layer,
                    sentence_analysis_layer=sentence_analysis_layer,
                    events=tuple(events),
                    no_op=grammar_note_layer is None and sentence_analysis_layer is None,
                )

    async def publish_article_grammar_batch(
        self,
        *,
        job_id: UUID,
        lease_token: UUID,
        outputs: list[tuple[str, GrammarBundleOutput]],
        quality_json: dict[str, Any] | None = None,
    ) -> PublishedGrammarBatch:
        """T4.1c compact grammar batch publish: wrap fence + N per-unit
        grammar_note/sentence_analysis writes in a ``publish_fence`` span.

        ``outputs`` is a list of ``(unit_id, GrammarBundleOutput)`` pairs
        produced by the batch worker. The publisher validates each per-unit
        output, inserts ``grammar_note`` and/or ``sentence_analysis``
        ``enhancement_layers`` rows per unit, and transitions the single
        batch job → ``succeeded``.
        """

        parent = current_span()
        recorder = get_default_recorder()
        publish_span = await recorder.start_span(
            trace_id=parent.trace_id if parent is not None else uuid4(),
            span_kind=SPAN_KIND_PUBLISH_FENCE,
            parent_span_id=parent.span_id if parent is not None else None,
            reader_job_id=job_id,
            metadata={"layer_type": "grammar_bundle", "batch": True},
        )
        try:
            result = await self._publish_article_grammar_batch_inner(
                job_id=job_id,
                lease_token=lease_token,
                outputs=outputs,
                quality_json=quality_json,
            )
            await recorder.end_span(
                publish_span,
                status=STATUS_SUCCEEDED,
                extra_metadata={
                    "unit_count": len(outputs),
                    "layer_count": len(result.layers),
                    "generation": result.generation,
                    "layer_ids": list(result.layer_ids),
                },
            )
            return result
        except FenceViolationError:
            await recorder.end_span(
                publish_span,
                status=STATUS_FAILED,
                failure_class="fence_violation",
                failure_code="fence_failed",
            )
            raise
        except Exception as exc:
            await recorder.end_span(
                publish_span,
                status=STATUS_FAILED,
                failure_class="publish_exception",
                failure_code=type(exc).__name__,
            )
            raise

    async def _publish_article_grammar_batch_inner(
        self,
        *,
        job_id: UUID,
        lease_token: UUID,
        outputs: list[tuple[str, GrammarBundleOutput]],
        quality_json: dict[str, Any] | None = None,
    ) -> PublishedGrammarBatch:
        quality_payload = dict(quality_json or {})
        published_at = datetime.now(UTC)

        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                job_row = await conn.fetchrow(
                    "SELECT * FROM reader_jobs WHERE id = $1 FOR UPDATE",
                    job_id,
                )
                if job_row is None:
                    raise LookupError(f"reader job {job_id} not found")
                if job_row["status"] != "claimed":
                    raise ValueError(
                        "grammar batch publish requires a claimed job"
                    )
                if (
                    job_row["job_type"] != GRAMMAR_BATCH_JOB_TYPE
                    or job_row["target_type"] != GRAMMAR_BATCH_TARGET_SCOPE
                ):
                    raise ValueError(
                        "grammar batch publish requires a "
                        "build_grammar_bundle/unit_range batch job"
                    )

                _assert_lease_valid(job_row, job_id, lease_token)
                fence_error = await self._job_runtime._validate_fence(conn, job_row)  # type: ignore[attr-defined]
                if fence_error is not None:
                    raise FenceViolationError(
                        f"publish fence failed for job {job_id}: {fence_error}"
                    )

                base_id = job_row["base_id"]
                if base_id is None:
                    raise FenceViolationError(
                        f"publish fence failed for job {job_id}: missing_base"
                    )
                reading_record_id = job_row["reading_record_id"]
                generation = int(job_row["expected_generation"])
                operation_fingerprint = str(job_row["operation_fingerprint"] or "")

                # The batch job's fingerprint may be either the SHORT_BATCH
                # base or the STRUCTURED_BATCH base (T4.1c route-specific).
                if not (
                    _fingerprint_matches_base(
                        operation_fingerprint,
                        GRAMMAR_BATCH_OPERATION_FINGERPRINT,
                    )
                    or _fingerprint_matches_base(
                        operation_fingerprint,
                        GRAMMAR_STRUCTURED_BATCH_OPERATION_FINGERPRINT,
                    )
                ):
                    raise ValueError(
                        "grammar batch publish fingerprint "
                        f"{operation_fingerprint!r} does not match either "
                        f"{GRAMMAR_BATCH_OPERATION_FINGERPRINT!r} or "
                        f"{GRAMMAR_STRUCTURED_BATCH_OPERATION_FINGERPRINT!r}"
                    )

                # Fail-closed: the batch output must cover exactly the units
                # listed in the job input_json ``target_unit_ids``.
                input_json = job_row["input_json"]
                target_unit_ids: list[str] = list(
                    input_json.get("target_unit_ids") or []
                )
                output_unit_ids = [unit_id for unit_id, _ in outputs]
                if sorted(target_unit_ids) != sorted(output_unit_ids):
                    raise ValueError(
                        "grammar batch output unit_ids "
                        f"{output_unit_ids!r} do not match target_unit_ids "
                        f"{target_unit_ids!r}"
                    )
                seen_unit_ids: set[str] = set()
                for unit_id, _ in outputs:
                    if unit_id in seen_unit_ids:
                        raise ValueError(
                            f"grammar batch output has duplicate unit_id {unit_id!r}"
                        )
                    seen_unit_ids.add(unit_id)

                outputs = _reorder_outputs_by_target_unit_ids(
                    outputs, target_unit_ids
                )

                published_layers: list[PublishedGrammarLayer] = []
                layer_ids: list[str] = []
                layer_types: list[str] = []
                event_ids: list[str] = []

                for unit_id, unit_output in outputs:
                    grammar_note_output = (
                        GrammarNoteLayerOutput(items=unit_output.grammar_notes)
                        if unit_output.grammar_notes
                        else None
                    )
                    sentence_analysis_output = (
                        SentenceAnalysisLayerOutput(
                            items=unit_output.sentence_analyses
                        )
                        if unit_output.sentence_analyses
                        else None
                    )

                    if grammar_note_output is not None:
                        await _assert_no_published_layer(
                            conn,
                            reading_record_id=reading_record_id,
                            base_id=base_id,
                            unit_id=unit_id,
                            generation=generation,
                            layer_type="grammar_note",
                        )
                        await _validate_grammar_note_items(
                            conn,
                            reading_record_id=reading_record_id,
                            base_id=base_id,
                            unit_id=unit_id,
                            output=grammar_note_output,
                        )
                    if sentence_analysis_output is not None:
                        await _assert_no_published_layer(
                            conn,
                            reading_record_id=reading_record_id,
                            base_id=base_id,
                            unit_id=unit_id,
                            generation=generation,
                            layer_type="sentence_analysis",
                        )
                        await _validate_sentence_analysis_items(
                            conn,
                            reading_record_id=reading_record_id,
                            base_id=base_id,
                            unit_id=unit_id,
                            output=sentence_analysis_output,
                        )

                    # Append ``:unit_id`` to the per-unit layer fingerprint
                    # so the ``uq_enhancement_layers_source_job_fingerprint``
                    # unique constraint ``(source_job_id, operation_fingerprint)``
                    # is not violated when N per-unit layers are published from
                    # one batch job.
                    if grammar_note_output is not None:
                        gn_layer = await _insert_published_grammar_layer(
                            conn,
                            event_runtime=self._event_runtime,
                            job_row=job_row,
                            job_id=job_id,
                            reading_record_id=reading_record_id,
                            base_id=base_id,
                            unit_id=unit_id,
                            generation=generation,
                            layer_type="grammar_note",
                            layer_operation_fingerprint=(
                                f"{GRAMMAR_NOTE_LAYER_OPERATION_FINGERPRINT}:{unit_id}"
                            ),
                            schema_version=int(grammar_note_output.schema_version),
                            payload=grammar_note_output.model_dump(mode="json"),
                            quality_json=quality_payload,
                            published_at=published_at,
                        )
                        published_layers.append(gn_layer)
                        layer_ids.append(str(gn_layer.layer_id))
                        layer_types.append("grammar_note")
                        event_ids.append(str(gn_layer.event.event_id))

                    if sentence_analysis_output is not None:
                        sa_layer = await _insert_published_grammar_layer(
                            conn,
                            event_runtime=self._event_runtime,
                            job_row=job_row,
                            job_id=job_id,
                            reading_record_id=reading_record_id,
                            base_id=base_id,
                            unit_id=unit_id,
                            generation=generation,
                            layer_type="sentence_analysis",
                            layer_operation_fingerprint=(
                                f"{SENTENCE_ANALYSIS_LAYER_OPERATION_FINGERPRINT}:{unit_id}"
                            ),
                            schema_version=int(sentence_analysis_output.schema_version),
                            payload=sentence_analysis_output.model_dump(mode="json"),
                            quality_json=quality_payload,
                            published_at=published_at,
                        )
                        published_layers.append(sa_layer)
                        layer_ids.append(str(sa_layer.layer_id))
                        layer_types.append("sentence_analysis")
                        event_ids.append(str(sa_layer.event.event_id))

                no_op = len(published_layers) == 0
                output_ref: dict[str, Any]
                rationale_code: str
                if no_op:
                    output_ref = {
                        "no_op": True,
                        "grammar_note_count": 0,
                        "sentence_analysis_count": 0,
                        "unit_count": len(outputs),
                    }
                    rationale_code = "grammar_batch_no_op"
                else:
                    output_ref = {
                        "layer_ids": layer_ids,
                        "layer_types": layer_types,
                        "event_ids": event_ids,
                        "unit_count": len(outputs),
                        "no_op": False,
                    }
                    rationale_code = "grammar_batch_published"

                updated_job = await self._job_runtime._apply_transition(  # type: ignore[attr-defined]
                    conn,
                    job_row=job_row,
                    target_status="succeeded",
                    available_at=None,
                    pause_owner=None,
                    output_ref=output_ref,
                    failure_class=None,
                    failure_code=None,
                    failure_message=None,
                    rationale_code=rationale_code,
                )
                await self._job_runtime._insert_job_event(  # type: ignore[attr-defined]
                    conn,
                    reading_record_id=updated_job["reading_record_id"],
                    run_id=updated_job["run_id"],
                    job_id=updated_job["id"],
                    event_type="job_succeeded",
                    payload={
                        "previous_status": job_row["status"],
                        "target_status": "succeeded",
                        "rationale_code": rationale_code,
                    },
                )
                await conn.execute(
                    """
                    UPDATE reader_runs
                    SET status = 'completed',
                        failure_class = NULL,
                        failure_code = NULL,
                        finished_at = $2,
                        updated_at = $2
                    WHERE id = $1
                    """,
                    job_row["run_id"],
                    published_at,
                )

                return PublishedGrammarBatch(
                    reading_record_id=reading_record_id,
                    base_id=base_id,
                    generation=generation,
                    layers=tuple(published_layers),
                    layer_ids=tuple(layer_ids),
                    layer_types=tuple(layer_types),
                    no_op=no_op,
                )


async def _assert_no_published_layer(
    conn: asyncpg.Connection,
    *,
    reading_record_id: UUID,
    base_id: UUID,
    unit_id: str,
    generation: int,
    layer_type: str,
) -> None:
    existing_layer = await conn.fetchrow(
        """
        SELECT id
        FROM enhancement_layers
        WHERE reading_record_id = $1
          AND base_id = $2
          AND layer_type = $3
          AND target_scope = 'unit'
          AND target_key = $4
          AND generation = $5
          AND status = 'published'
        LIMIT 1
        """,
        reading_record_id,
        base_id,
        layer_type,
        unit_id,
        generation,
    )
    if existing_layer is not None:
        raise ValueError(f"{layer_type} layer already published for unit {unit_id}")


def _reorder_outputs_by_target_unit_ids(
    outputs: list[tuple[str, _T]],
    target_unit_ids: list[str],
) -> list[tuple[str, _T]]:
    """Reorder batch outputs to match ``target_unit_ids`` (reading order).

    T1 acceptance: the batch executor may return unit outputs in any order
    (e.g. parallel LLM call completion order). The publisher must publish
    per-unit layers/events in reading order so the frontend snapshot reload
    sees layers appear in the same order the reader reads them. The unit set
    has already been validated by the caller, so every ``target_unit_ids``
    entry has exactly one matching output.
    """
    output_by_unit: dict[str, _T] = {uid: out for uid, out in outputs}
    return [(uid, output_by_unit[uid]) for uid in target_unit_ids]


def _build_translation_coverage_summary(
    *,
    unit_id: str,
    generation: int,
    validation: _ValidatedTranslationOutput,
) -> dict[str, Any]:
    return {
        "coverage_status": "complete",
        "unit_id": unit_id,
        "generation": generation,
        "group_count": len(validation.groups),
        "covered_anchor_segment_ids": list(validation.covered_anchor_segment_ids),
        "missing_anchor_segment_ids": list(validation.missing_anchor_segment_ids),
        "groups": [
            {
                "group_id": group.group_id,
                "anchor_segment_ids": list(group.anchor_segment_ids),
                "source_text_hash": group.source_text_hash,
                "translated_text_length": group.translated_text_length,
            }
            for group in validation.groups
        ],
    }


async def _validate_translation_output_for_publish(
    conn: asyncpg.Connection,
    *,
    reading_record_id: UUID,
    base_id: UUID,
    unit_id: str,
    operation_fingerprint: str,
    output: Any,
) -> _ValidatedTranslationOutput:
    if not _fingerprint_matches_base(
        operation_fingerprint,
        TRANSLATION_OPERATION_FINGERPRINT,
    ):
        raise TranslationPublishValidationError(
            "translation_fingerprint_mismatch",
            (
                f"translation publish fingerprint {operation_fingerprint!r} "
                f"does not match {TRANSLATION_OPERATION_FINGERPRINT!r}"
            ),
        )

    try:
        parsed_output = TranslationLayerOutput.model_validate(output)
    except ValidationError as exc:
        raise TranslationPublishValidationError(
            "translation_invalid_output_schema",
            "translation output must match current group-native TranslationLayerOutput",
        ) from exc

    return await _validate_translation_unit_output_core(
        conn,
        reading_record_id=reading_record_id,
        base_id=base_id,
        unit_id=unit_id,
        parsed_output=parsed_output,
    )


async def _validate_translation_unit_output_core(
    conn: asyncpg.Connection,
    *,
    reading_record_id: UUID,
    base_id: UUID,
    unit_id: str,
    parsed_output: TranslationLayerOutput,
) -> _ValidatedTranslationOutput:
    """Validate a parsed translation output against unit anchor segments.

    T1.1 short-article batch path: the batch publisher splits the LLM output
    into per-unit :class:`TranslationLayerOutput` objects and calls this
    core for each unit. The per-unit fingerprint check is intentionally NOT
    performed here; the batch publish method validates the batch job
    fingerprint once at the top.
    """
    context = await _load_unit_anchor_validation_context(
        conn,
        reading_record_id=reading_record_id,
        base_id=base_id,
        unit_id=unit_id,
    )

    covered_anchor_segment_ids: list[str] = []
    covered_anchor_segment_id_set: set[str] = set()
    seen_group_ids: set[str] = set()
    validated_groups: list[_ValidatedTranslationGroup] = []
    last_group_end_order = 0

    for group in parsed_output.groups:
        if not group.group_id.strip():
            raise TranslationPublishValidationError(
                "translation_invalid_group_id",
                "translation group_id must be non-empty",
            )
        if group.group_id in seen_group_ids:
            raise TranslationPublishValidationError(
                "translation_duplicate_group_id",
                f"translation group_id {group.group_id!r} is duplicated",
            )
        seen_group_ids.add(group.group_id)

        if not group.translated_text.strip():
            raise TranslationPublishValidationError(
                "translation_empty_translated_text",
                f"translation group {group.group_id!r} translated_text must not be blank",
            )

        segment_rows: list[asyncpg.Record] = []
        for anchor_segment_id in group.anchor_segment_ids:
            segment_row = context.segments_by_id.get(anchor_segment_id)
            if segment_row is None:
                raise TranslationPublishValidationError(
                    "translation_unknown_anchor_segment",
                    (
                        f"translation group {group.group_id!r} references unknown "
                        f"anchor_segment_id {anchor_segment_id!r}"
                    ),
                )
            segment_rows.append(segment_row)

        order_indexes = [int(row["order_index"]) for row in segment_rows]
        if order_indexes != sorted(order_indexes) or any(
            current != previous + 1
            for previous, current in zip(order_indexes, order_indexes[1:], strict=False)
        ):
            raise TranslationPublishValidationError(
                "translation_group_non_contiguous",
                (
                    f"translation group {group.group_id!r} anchor segments must be "
                    "ordered and contiguous"
                ),
            )

        first_row = segment_rows[0]
        last_row = segment_rows[-1]
        first_order = int(first_row["order_index"])
        last_order = int(last_row["order_index"])
        if first_order <= last_group_end_order:
            raise TranslationPublishValidationError(
                "translation_group_overlap",
                (
                    f"translation group {group.group_id!r} overlaps or reorders "
                    "previous translation coverage"
                ),
            )

        span_start = int(first_row["unit_start_utf16"])
        span_end = int(last_row["unit_end_utf16"])
        if span_start > span_end:
            raise TranslationPublishValidationError(
                "translation_group_span_inverted",
                (
                    f"translation group {group.group_id!r} has inverted span "
                    f"{span_start}:{span_end}"
                ),
            )

        span_text = slice_by_utf16_offsets(context.unit_text, span_start, span_end)
        if span_text is None or not span_text:
            raise TranslationPublishValidationError(
                "translation_group_slice_failed",
                (
                    f"translation group {group.group_id!r} span "
                    f"{span_start}:{span_end} could not be sliced from unit {unit_id!r}"
                ),
            )
        if compute_text_range_hash(span_text) != group.source_text_hash:
            raise TranslationPublishValidationError(
                "translation_group_hash_mismatch",
                (
                    f"translation group {group.group_id!r} source_text_hash does "
                    "not match the stable unit span slice"
                ),
            )

        for anchor_segment_id in group.anchor_segment_ids:
            if anchor_segment_id in covered_anchor_segment_id_set:
                raise TranslationPublishValidationError(
                    "translation_group_overlap",
                    (
                        f"translation group {group.group_id!r} reuses "
                        f"anchor_segment_id {anchor_segment_id!r}"
                    ),
                )
            covered_anchor_segment_id_set.add(anchor_segment_id)
            covered_anchor_segment_ids.append(anchor_segment_id)
        last_group_end_order = last_order
        validated_groups.append(
            _ValidatedTranslationGroup(
                group_id=group.group_id,
                anchor_segment_ids=tuple(group.anchor_segment_ids),
                source_text_hash=group.source_text_hash,
                translated_text_length=len(group.translated_text),
            )
        )

    expected_anchor_segment_ids = tuple(
        str(row["anchor_segment_id"])
        for row in context.ordered_segments
    )
    missing_anchor_segment_ids = tuple(
        anchor_segment_id
        for anchor_segment_id in expected_anchor_segment_ids
        if anchor_segment_id not in covered_anchor_segment_id_set
    )
    if missing_anchor_segment_ids:
        raise TranslationPublishValidationError(
            "translation_missing_anchor_coverage",
            (
                f"translation output for unit {unit_id!r} is missing anchor coverage "
                f"for {list(missing_anchor_segment_ids)!r}"
            ),
        )

    return _ValidatedTranslationOutput(
        output=parsed_output,
        covered_anchor_segment_ids=expected_anchor_segment_ids,
        missing_anchor_segment_ids=missing_anchor_segment_ids,
        groups=tuple(validated_groups),
    )


async def _insert_published_grammar_layer(
    conn: asyncpg.Connection,
    *,
    event_runtime: ReaderEventRuntime,
    job_row: asyncpg.Record,
    job_id: UUID,
    reading_record_id: UUID,
    base_id: UUID,
    unit_id: str,
    generation: int,
    layer_type: str,
    layer_operation_fingerprint: str,
    schema_version: int,
    payload: dict[str, Any],
    quality_json: dict[str, Any],
    published_at: datetime,
) -> PublishedGrammarLayer:
    layer_row = await conn.fetchrow(
        """
        INSERT INTO enhancement_layers (
            reading_record_id,
            base_id,
            layer_type,
            layer_subtype,
            target_scope,
            target_key,
            generation,
            status,
            operation_fingerprint,
            schema_version,
            output_json,
            coverage_json,
            quality_json,
            source_run_id,
            source_job_id,
            published_at
        )
        VALUES (
            $1,
            $2,
            $3,
            NULL,
            'unit',
            $4,
            $5,
            'published',
            $6,
            $7,
            $8::jsonb,
            '{}'::jsonb,
            $9::jsonb,
            $10,
            $11,
            $12
        )
        RETURNING id
        """,
        reading_record_id,
        base_id,
        layer_type,
        unit_id,
        generation,
        layer_operation_fingerprint,
        schema_version,
        jsonb_param(payload),
        jsonb_param(quality_json),
        job_row["run_id"],
        job_id,
        published_at,
    )
    if layer_row is None:
        raise RuntimeError("enhancement_layers insert did not return a row")

    # T4.2a-PUX-R4-R2.2-P2b-R1: grammar_note 首发使用扩展 payload
    # （schema_version / operation / insertions[]），由 builder 从 typed
    # GrammarNoteLayerOutput 自动派生，validator 在同事务内、event 写入前校验。
    # sentence_analysis 保持既有 7 字段 payload，不接入 builder/validator。
    if layer_type == "grammar_note":
        # 查询 source anchor 顺序（order_index ASC），作为 descriptor 排序依据。
        segment_rows = await conn.fetch(
            """
            SELECT anchor_segment_id
            FROM anchor_segments
            WHERE reading_record_id = $1
              AND base_id = $2
              AND unit_id = $3
            ORDER BY order_index ASC
            """,
            reading_record_id,
            base_id,
            unit_id,
        )
        anchor_order = tuple(
            str(row["anchor_segment_id"]) for row in segment_rows
        )

        base_payload = {
            "record_id": str(reading_record_id),
            "base_id": str(base_id),
            "layer_id": str(layer_row["id"]),
            "layer_type": layer_type,
            "target_scope": "unit",
            "target_key": unit_id,
            "generation": generation,
        }
        event_payload = build_grammar_layer_published_payload(
            base_payload=base_payload,
            layer_id=str(layer_row["id"]),
            layer_type=layer_type,
            target_key=unit_id,
            typed_output=payload,
            anchor_order=anchor_order,
        )
        # 校验失败抛 ValueError，事务回滚：layer INSERT / event INSERT /
        # sequence 增量全部回滚，sequence 不推进。
        validate_grammar_layer_published_payload(event_payload)
    else:
        event_payload = {
            "record_id": str(reading_record_id),
            "base_id": str(base_id),
            "layer_id": str(layer_row["id"]),
            "layer_type": layer_type,
            "target_scope": "unit",
            "target_key": unit_id,
            "generation": generation,
        }

    event = await event_runtime.publish_event_in_transaction(
        conn,
        record_id=reading_record_id,
        event_type="layer_published",
        payload_json=event_payload,
        source_run_id=job_row["run_id"],
        source_job_id=job_id,
        source_layer_id=layer_row["id"],
        created_at=published_at,
    )
    return PublishedGrammarLayer(
        layer_id=layer_row["id"],
        layer_type=layer_type,
        reading_record_id=reading_record_id,
        base_id=base_id,
        unit_id=unit_id,
        generation=generation,
        event=event,
    )


async def _load_unit_anchor_validation_context(
    conn: asyncpg.Connection,
    *,
    reading_record_id: UUID,
    base_id: UUID,
    unit_id: str,
) -> _UnitAnchorValidationContext:
    unit_row = await conn.fetchrow(
        """
        SELECT unit.unit_id,
               unit.text_hash,
               base.text AS base_text,
               unit.base_start_utf16,
               unit.base_end_utf16
        FROM reading_units unit
        JOIN reading_bases base
          ON base.id = unit.base_id
         AND base.reading_record_id = unit.reading_record_id
        WHERE unit.reading_record_id = $1
          AND unit.base_id = $2
          AND unit.unit_id = $3
        """,
        reading_record_id,
        base_id,
        unit_id,
    )
    if unit_row is None:
        raise ValueError(f"publish target unit {unit_id} does not exist")

    unit_text = slice_by_utf16_offsets(
        str(unit_row["base_text"]),
        int(unit_row["base_start_utf16"]),
        int(unit_row["base_end_utf16"]),
    )
    if unit_text is None or not unit_text:
        raise ValueError(f"publish target unit {unit_id} could not be sliced")

    segment_rows = await conn.fetch(
        """
        SELECT anchor_segment_id,
               sentence_id,
               order_index,
               segment_type,
               unit_start_utf16,
               unit_end_utf16,
               text_hash
        FROM anchor_segments
        WHERE reading_record_id = $1
          AND base_id = $2
          AND unit_id = $3
        ORDER BY order_index ASC
        """,
        reading_record_id,
        base_id,
        unit_id,
    )
    segments_by_id = {
        str(row["anchor_segment_id"]): row
        for row in segment_rows
    }
    if not segments_by_id:
        raise ValueError(f"publish target unit {unit_id} has no anchor segments")

    return _UnitAnchorValidationContext(
        unit_id=str(unit_row["unit_id"]),
        unit_text=unit_text,
        unit_text_hash=str(unit_row["text_hash"]),
        ordered_segments=tuple(segment_rows),
        segments_by_id=segments_by_id,
    )


def _validate_text_range_anchor(
    context: _UnitAnchorValidationContext,
    *,
    item_label: str,
    anchor: ReaderTextRangeAnchor,
    base_id: UUID,
    unit_id: str,
) -> None:
    if anchor.base_id != str(base_id):
        raise ValueError(
            f"{item_label} base_id {anchor.base_id} does not match publish base {base_id}"
        )
    if anchor.unit_id != unit_id:
        raise ValueError(
            f"{item_label} unit_id {anchor.unit_id} does not match publish unit {unit_id}"
        )

    segment_row = context.segments_by_id.get(anchor.anchor_segment_id)
    if segment_row is None:
        raise ValueError(
            f"{item_label} anchor_segment_id {anchor.anchor_segment_id} does not exist"
        )

    expected_sentence_id = str(segment_row["sentence_id"] or segment_row["anchor_segment_id"])
    if anchor.sentence_id is not None and anchor.sentence_id != expected_sentence_id:
        raise ValueError(
            f"{item_label} sentence_id {anchor.sentence_id} "
            f"does not match anchor segment {expected_sentence_id}"
        )
    if anchor.segment_type != str(segment_row["segment_type"]):
        raise ValueError(
            f"{item_label} segment_type {anchor.segment_type} "
            f"does not match stored segment type {segment_row['segment_type']}"
        )

    segment_start = int(segment_row["unit_start_utf16"])
    segment_end = int(segment_row["unit_end_utf16"])
    if anchor.start_offset < segment_start or anchor.end_offset > segment_end:
        raise ValueError(
            f"{item_label} offsets fall outside anchor segment {anchor.anchor_segment_id}"
        )

    segment_text = slice_by_utf16_offsets(context.unit_text, segment_start, segment_end)
    if segment_text is None or not segment_text:
        raise ValueError(
            f"{item_label} anchor segment {anchor.anchor_segment_id} "
            "could not be sliced from unit text"
        )
    if compute_text_range_hash(segment_text) != str(segment_row["text_hash"]):
        raise ValueError(
            f"{item_label} anchor segment {anchor.anchor_segment_id} hash mismatch"
        )

    selected_text = slice_by_utf16_offsets(
        context.unit_text,
        anchor.start_offset,
        anchor.end_offset,
    )
    if selected_text is None:
        raise ValueError(f"{item_label} offsets do not slice target unit {unit_id}")
    if selected_text != anchor.selected_text:
        raise ValueError(f"{item_label} selected_text does not match target unit {unit_id}")
    if compute_text_range_hash(selected_text) != anchor.text_hash:
        raise ValueError(f"{item_label} text_hash does not match target unit {unit_id}")


async def _validate_vocabulary_items(
    conn: asyncpg.Connection,
    *,
    reading_record_id: UUID,
    base_id: UUID,
    unit_id: str,
    output: VocabularyLayerOutput,
) -> None:
    if not output.items:
        return

    context = await _load_unit_anchor_validation_context(
        conn,
        reading_record_id=reading_record_id,
        base_id=base_id,
        unit_id=unit_id,
    )
    for item in output.items:
        _validate_text_range_anchor(
            context,
            item_label=f"vocabulary item {item.item_type}",
            anchor=item.anchor,
            base_id=base_id,
            unit_id=unit_id,
        )


async def _validate_grammar_note_items(
    conn: asyncpg.Connection,
    *,
    reading_record_id: UUID,
    base_id: UUID,
    unit_id: str,
    output: GrammarNoteLayerOutput,
) -> None:
    context = await _load_unit_anchor_validation_context(
        conn,
        reading_record_id=reading_record_id,
        base_id=base_id,
        unit_id=unit_id,
    )
    for item in output.items:
        for span_index, span in enumerate(item.spans):
            _validate_text_range_anchor(
                context,
                item_label=(
                    f"grammar_note item {item.grammar_point} span[{span_index}]"
                ),
                anchor=span,
                base_id=base_id,
                unit_id=unit_id,
            )


async def _validate_sentence_analysis_items(
    conn: asyncpg.Connection,
    *,
    reading_record_id: UUID,
    base_id: UUID,
    unit_id: str,
    output: SentenceAnalysisLayerOutput,
) -> None:
    context = await _load_unit_anchor_validation_context(
        conn,
        reading_record_id=reading_record_id,
        base_id=base_id,
        unit_id=unit_id,
    )
    for item in output.items:
        _validate_text_range_anchor(
            context,
            item_label=f"sentence_analysis item {item.label}",
            anchor=item.anchor,
            base_id=base_id,
            unit_id=unit_id,
        )
        for chunk in item.chunks:
            if item.anchor.selected_text.find(chunk.text) < 0:
                raise ValueError(
                    f"sentence_analysis item {item.label} chunk text "
                    f"{chunk.text!r} is not grounded in anchor selected_text"
                )
