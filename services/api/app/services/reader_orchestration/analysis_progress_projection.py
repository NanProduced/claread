"""Canonical read-only Reader analysis progress projection.

Rebuilds ``ReaderAnalysisProgress`` from active-base PostgreSQL facts.
Does not write, bootstrap, or call providers.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import asyncpg

from app.contracts.annotation import slice_by_utf16_offsets
from app.database import connection as db_connection
from app.schemas.reader_orchestration import (
    ReaderAnalysisActivePhase,
    ReaderAnalysisCapabilityStatus,
    ReaderAnalysisMode,
    ReaderAnalysisOverallStatus,
    ReaderAnalysisProgress,
    ReaderAnalysisSectionProgress,
)
from app.services.reader_orchestration.analysis_section_jobs import (
    ANALYSIS_SECTION_REQUEST_ORIGIN,
    TRANSLATION_NON_TERMINAL_STATUSES,
    TRANSLATION_TERMINAL_GATE_JOB_TYPES,
)
from app.services.reader_orchestration.analysis_section_plan import (
    ANALYSIS_SECTION_PLAN_VERSION,
    AnalysisSection,
    AnalysisSectionUnit,
    plan_analysis_sections,
)
from app.services.reader_orchestration.automatic_layer_policy import (
    filter_units_for_automatic_layer,
)
from app.services.reader_orchestration.document_feature_extractor import (
    ArticleRoute,
    classify_article_route,
    extract_document_features,
)
from app.services.reader_orchestration.reading_strategy import (
    resolve_reader_variant_strategy,
)

EXCERPT_MAX_CHARS = 80
EXCERPT_ELLIPSIS = "…"
MALFORMED_JOB_FAILURE_CODE = "malformed_analysis_job_input"
INCONSISTENT_ACTIVE_BASE = "inconsistent_active_base"
RECORD_NOT_FOUND = "record_not_found"
ANALYSIS_JOB_FAILED = "analysis_job_failed"
ANALYSIS_JOB_PAUSED = "analysis_job_paused"
BUDGET_EXHAUSTED_CODE = "budget_exhausted"
QUOTA_PAUSE_OWNER = "quota"
_FAILURE_VISIBLE_STATUSES = frozenset({"partial", "failed", "paused_quota"})

_TRANSLATION_JOB_TYPES = frozenset(TRANSLATION_TERMINAL_GATE_JOB_TYPES)
_VOCABULARY_JOB_TYPES = frozenset(
    {"build_vocabulary_layer", "build_vocabulary_layer_article"}
)
_GRAMMAR_JOB_TYPES = frozenset(
    {"build_grammar_bundle", "build_grammar_bundle_window"}
)
_BATCH_JOB_TYPES = frozenset(
    {
        "translate_article",
        "build_vocabulary_layer_article",
        "build_grammar_bundle",
        "build_grammar_bundle_window",
    }
)
_PER_UNIT_JOB_TYPES = frozenset({"translate_unit", "build_vocabulary_layer"})
_IGNORED_JOB_STATUSES = frozenset({"cancelled", "superseded"})
_SUCCESS_JOB_STATUSES = frozenset({"succeeded", "skipped"})
_FAILURE_JOB_STATUSES = frozenset({"failed_terminal"})
_QUEUED_JOB_STATUSES = frozenset({"queued", "retry_later"})
_ACTIVE_SECTION_STATUSES = frozenset({"queued", "processing"})
_FIRST_SECTION_READY_STATUSES = frozenset({"completed", "partial", "failed"})
CapabilityBucket = Literal["translation", "vocabulary", "grammar"]


class AnalysisProgressProjectionError(Exception):
    """Fail-closed projection error with a stable code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class CapabilityJobFact:
    job_type: str
    status: str
    pause_owner: str | None
    rationale_code: str | None
    failure_code: str | None
    updated_at: datetime | None
    malformed: bool = False
    captured_resume_ready: bool = False


@dataclass
class _CapabilityFacts:
    eligible: frozenset[str]
    jobs: list[CapabilityJobFact] = field(default_factory=list)
    completed_ids: set[str] = field(default_factory=set)
    timestamps: list[datetime] = field(default_factory=list)


def build_analysis_excerpt(source_text: str) -> str:
    """Collapse whitespace and cap at 80 Unicode characters."""
    collapsed = " ".join(source_text.split())
    if len(collapsed) <= EXCERPT_MAX_CHARS:
        return collapsed
    return collapsed[:EXCERPT_MAX_CHARS] + EXCERPT_ELLIPSIS


def reduce_capability_status(
    *,
    eligible_ids: frozenset[str],
    jobs: list[CapabilityJobFact],
    completed_ids: set[str],
) -> tuple[ReaderAnalysisCapabilityStatus, str | None]:
    """Single capability reducer. Priority is the product contract."""
    live = [job for job in jobs if not job.malformed]
    if any(job.status == "claimed" for job in live):
        return "processing", None
    if any(_is_quota_pause(job) or _is_budget_exhausted(job) for job in jobs):
        return "paused_quota", BUDGET_EXHAUSTED_CODE
    if any(job.status in _QUEUED_JOB_STATUSES for job in live):
        return "queued", None
    if any(job.captured_resume_ready for job in live):
        return "queued", None
    other_paused = [
        job for job in live if job.status == "paused" and not job.captured_resume_ready
    ]
    if other_paused:
        return "failed", _stable_failure_code(other_paused[-1])
    if not eligible_ids:
        return "completed", None
    if eligible_ids <= completed_ids:
        return "completed", None
    failures = [
        job for job in jobs if job.malformed or job.status in _FAILURE_JOB_STATUSES
    ]
    covered = completed_ids & eligible_ids
    if covered and failures:
        return "partial", _latest_failure_code(failures)
    if covered:
        return "partial", None
    if failures:
        return "failed", _latest_failure_code(failures)
    return "not_started", None


class ReaderAnalysisProgressProjection:
    def __init__(self, *, pool: asyncpg.Pool | None = None) -> None:
        self._pool = pool

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    async def load_progress(
        self,
        *,
        record_id: UUID,
        user_id: UUID,
    ) -> ReaderAnalysisProgress:
        async with self.get_pool().acquire() as conn:
            async with conn.transaction(isolation="repeatable_read", readonly=True):
                return await self.load_progress_on_connection(
                    conn, record_id=record_id, user_id=user_id
                )

    async def load_progress_on_connection(
        self,
        conn: asyncpg.Connection,
        *,
        record_id: UUID,
        user_id: UUID,
    ) -> ReaderAnalysisProgress:
        return await _load_progress(conn, record_id=record_id, user_id=user_id)


async def _load_progress(
    conn: asyncpg.Connection,
    *,
    record_id: UUID,
    user_id: UUID,
) -> ReaderAnalysisProgress:
    record = await conn.fetchrow(
        """
        SELECT id, generation, active_base_id, reading_goal, reading_variant
        FROM reading_records
        WHERE id = $1
          AND user_id = $2
          AND deleted_at IS NULL
        """,
        record_id,
        user_id,
    )
    if record is None:
        raise LookupError(RECORD_NOT_FOUND)
    if record["active_base_id"] is None:
        raise AnalysisProgressProjectionError(INCONSISTENT_ACTIVE_BASE)
    base = await conn.fetchrow(
        """
        SELECT id, text, record_generation, status
        FROM reading_bases
        WHERE id = $1
          AND reading_record_id = $2
        """,
        record["active_base_id"],
        record_id,
    )
    if (
        base is None
        or str(base["status"]) != "active"
        or int(base["record_generation"]) != int(record["generation"])
    ):
        raise AnalysisProgressProjectionError(INCONSISTENT_ACTIVE_BASE)
    units = await conn.fetch(
        """
        SELECT unit_id, order_index, unit_type,
               base_start_utf16, base_end_utf16, metadata_json
        FROM reading_units
        WHERE reading_record_id = $1
          AND base_id = $2
        ORDER BY order_index ASC
        """,
        record_id,
        base["id"],
    )
    if not units:
        raise AnalysisProgressProjectionError(INCONSISTENT_ACTIVE_BASE)
    jobs = await conn.fetch(
        """
        SELECT job.job_type, job.target_type, job.target_key, job.status,
               job.pause_owner, job.rationale_code, job.failure_code,
               job.input_json, job.updated_at, job.created_at,
               (
                   job.status = 'paused'
                   AND job.pause_owner = 'system'
                   AND job.rationale_code =
                       'model_execution_captured_resume_required'
                   AND job.failure_class = 'model_execution'
                   AND job.failure_code = 'post_provider_resume_required'
                   AND EXISTS (
                       SELECT 1
                       FROM ai_model_execution_journal journal
                       WHERE journal.reader_job_id = job.id
                         AND journal.attempt_ordinal = job.attempt_count
                         AND journal.capture_state = 'captured'
                   )
               ) AS captured_resume_ready
        FROM reader_jobs job
        WHERE job.reading_record_id = $1
          AND job.base_id = $2
          AND job.expected_generation = $3
        """,
        record_id,
        base["id"],
        int(record["generation"]),
    )
    layers = await conn.fetch(
        """
        SELECT layer_type, target_scope, target_key, status,
               updated_at, published_at
        FROM enhancement_layers
        WHERE reading_record_id = $1
          AND base_id = $2
          AND generation = $3
          AND status = 'published'
        """,
        record_id,
        base["id"],
        int(record["generation"]),
    )
    return _project(record=record, base=base, units=units, jobs=jobs, layers=layers)


def _project(
    *,
    record: asyncpg.Record,
    base: asyncpg.Record,
    units: list[asyncpg.Record],
    jobs: list[asyncpg.Record],
    layers: list[asyncpg.Record],
) -> ReaderAnalysisProgress:
    unit_maps = [_unit_map(row) for row in units]
    planned = plan_analysis_sections(
        str(base["id"]),
        [
            AnalysisSectionUnit(
                unit_id=unit["unit_id"],
                order_index=unit["order_index"],
                text_length=unit["text_length"],
            )
            for unit in unit_maps
        ],
    )
    if not planned:
        raise AnalysisProgressProjectionError(INCONSISTENT_ACTIVE_BASE)
    unit_to_section = {
        unit_id: section.order_index
        for section in planned
        for unit_id in section.target_unit_ids
    }
    planned_by_id = {section.section_id: section for section in planned}
    strategy = resolve_reader_variant_strategy(
        str(record["reading_goal"]),
        str(record["reading_variant"]),
    )
    route = classify_article_route(
        extract_document_features(
            base_text=str(base["text"] or ""),
            unit_types=tuple(unit["unit_type"] for unit in unit_maps),
            reading_goal=strategy.reading_goal,
            reading_variant=strategy.reading_variant,
            requested_layers=tuple(strategy.layers.keys()),
        )
    )
    mode: ReaderAnalysisMode = (
        "segmented_on_demand"
        if route is ArticleRoute.GROUPED_WINDOWED
        else "automatic"
    )
    vocab_eligible = _layer_eligible(unit_maps, "vocabulary")
    grammar_eligible = _grammar_eligible(unit_maps)
    translation_facts = _CapabilityFacts(
        eligible=_layer_eligible(unit_maps, "translation")
    )
    section_vocab = [
        _CapabilityFacts(eligible=_intersect(vocab_eligible, section.target_unit_ids))
        for section in planned
    ]
    section_grammar = [
        _CapabilityFacts(eligible=_intersect(grammar_eligible, section.target_unit_ids))
        for section in planned
    ]
    for layer in layers:
        _consume_layer(
            layer,
            unit_to_section=unit_to_section,
            translation_facts=translation_facts,
            section_vocab=section_vocab,
            section_grammar=section_grammar,
        )
    for job in jobs:
        _consume_job(
            job,
            planned_by_id=planned_by_id,
            unit_to_section=unit_to_section,
            translation_facts=translation_facts,
            section_vocab=section_vocab,
            section_grammar=section_grammar,
        )

    translation_status, _ = reduce_capability_status(
        eligible_ids=translation_facts.eligible,
        jobs=translation_facts.jobs,
        completed_ids=translation_facts.completed_ids,
    )
    nonterminal_translation = any(
        job.status in TRANSLATION_NON_TERMINAL_STATUSES
        for job in translation_facts.jobs
    )
    rows: list[ReaderAnalysisSectionProgress] = []
    for section, vocab_facts, grammar_facts in zip(
        planned, section_vocab, section_grammar, strict=True
    ):
        vocab_status, vocab_failure = reduce_capability_status(
            eligible_ids=vocab_facts.eligible,
            jobs=vocab_facts.jobs,
            completed_ids=vocab_facts.completed_ids,
        )
        grammar_status, grammar_failure = reduce_capability_status(
            eligible_ids=grammar_facts.eligible,
            jobs=grammar_facts.jobs,
            completed_ids=grammar_facts.completed_ids,
        )
        status = _reduce_section_status(vocab_status, grammar_status)
        rows.append(
            ReaderAnalysisSectionProgress(
                section_id=section.section_id,
                order_index=section.order_index,
                label=section.label,
                excerpt=_section_excerpt(str(base["text"] or ""), unit_maps, section),
                start_unit_id=section.start_unit_id,
                end_unit_id=section.end_unit_id,
                status=status,
                vocabulary_status=vocab_status,
                grammar_status=grammar_status,
                can_start=False,
                updated_at=_max_time(vocab_facts.timestamps + grammar_facts.timestamps),
                failure_code=_section_failure_code(
                    status, vocab_failure, grammar_failure
                ),
            )
        )
    if mode == "segmented_on_demand":
        first_status = rows[0].status
        for index, row in enumerate(rows):
            row.can_start = _can_start(
                section_status=row.status,
                is_first=index == 0,
                first_section_status=first_status,
                nonterminal_translation=nonterminal_translation,
            )
    active_section_id = next(
        (row.section_id for row in rows if row.status in _ACTIVE_SECTION_STATUSES),
        None,
    )
    translation_active = _is_active_status(translation_status)
    analysis_active = any(row.status in _ACTIVE_SECTION_STATUSES for row in rows)
    if translation_active:
        active_phase: ReaderAnalysisActivePhase | None = "translation"
    elif analysis_active:
        active_phase = "analysis"
    else:
        active_phase = None
    overall = _reduce_overall(
        mode=mode,
        translation_status=translation_status,
        section_statuses=[row.status for row in rows],
        any_can_start=any(row.can_start for row in rows),
        translation_jobs=translation_facts.jobs,
    )
    last_progress_at = _max_time(
        translation_facts.timestamps
        + [stamp for facts in section_vocab for stamp in facts.timestamps]
        + [stamp for facts in section_grammar for stamp in facts.timestamps]
    )
    return ReaderAnalysisProgress(
        mode=mode,
        plan_version=ANALYSIS_SECTION_PLAN_VERSION,
        overall_status=overall,
        active_phase=active_phase,
        translation_status=translation_status,
        completed_section_count=sum(1 for row in rows if row.status == "completed"),
        total_section_count=len(rows),
        active_section_id=active_section_id,
        needs_user_action=_needs_user_action(
            overall=overall,
            capability_statuses=[
                translation_status,
                *[row.status for row in rows],
                *[row.vocabulary_status for row in rows],
                *[row.grammar_status for row in rows],
            ],
            jobs=(
                translation_facts.jobs
                + [job for facts in section_vocab for job in facts.jobs]
                + [job for facts in section_grammar for job in facts.jobs]
            ),
        ),
        last_progress_at=last_progress_at,
        sections=rows,
    )


def _unit_map(row: asyncpg.Record) -> dict[str, Any]:
    start = int(row["base_start_utf16"])
    end = int(row["base_end_utf16"])
    return {
        "unit_id": str(row["unit_id"]),
        "order_index": int(row["order_index"]),
        "unit_type": str(row["unit_type"]),
        "base_start_utf16": start,
        "base_end_utf16": end,
        "text_length": end - start,
        "metadata_json": row["metadata_json"],
    }


def _layer_eligible(
    units: list[dict[str, Any]],
    layer: Literal["translation", "vocabulary", "grammar_note", "sentence_analysis"],
) -> frozenset[str]:
    kept = filter_units_for_automatic_layer(units, layer, shadow_log=False)
    return frozenset(str(unit["unit_id"]) for unit in kept)


def _grammar_eligible(units: list[dict[str, Any]]) -> frozenset[str]:
    return _layer_eligible(units, "grammar_note") | _layer_eligible(
        units, "sentence_analysis"
    )


def _intersect(eligible: frozenset[str], unit_ids: tuple[str, ...]) -> frozenset[str]:
    return eligible.intersection(unit_ids)


def _section_excerpt(
    base_text: str,
    units: list[dict[str, Any]],
    section: AnalysisSection,
) -> str:
    first = next(unit for unit in units if unit["unit_id"] == section.start_unit_id)
    sliced = slice_by_utf16_offsets(
        base_text, first["base_start_utf16"], first["base_end_utf16"]
    )
    return "" if sliced is None else build_analysis_excerpt(sliced)


def _consume_layer(
    layer: asyncpg.Record,
    *,
    unit_to_section: dict[str, int],
    translation_facts: _CapabilityFacts,
    section_vocab: list[_CapabilityFacts],
    section_grammar: list[_CapabilityFacts],
) -> None:
    if str(layer.get("target_scope") or "unit") != "unit":
        return
    unit_id = str(layer["target_key"])
    stamp = layer["published_at"] or layer["updated_at"]
    layer_type = str(layer["layer_type"])
    if layer_type == "translation":
        translation_facts.completed_ids.add(unit_id)
        _add_time(translation_facts, stamp)
        return
    index = unit_to_section.get(unit_id)
    if index is None:
        return
    if layer_type == "vocabulary":
        section_vocab[index].completed_ids.add(unit_id)
        _add_time(section_vocab[index], stamp)
    elif layer_type in {"grammar_note", "sentence_analysis"}:
        section_grammar[index].completed_ids.add(unit_id)
        _add_time(section_grammar[index], stamp)


def _consume_job(
    job: asyncpg.Record,
    *,
    planned_by_id: dict[str, AnalysisSection],
    unit_to_section: dict[str, int],
    translation_facts: _CapabilityFacts,
    section_vocab: list[_CapabilityFacts],
    section_grammar: list[_CapabilityFacts],
) -> None:
    if str(job["status"]) in _IGNORED_JOB_STATUSES:
        return
    bucket = _bucket_for_job(str(job["job_type"]))
    if bucket is None:
        return
    parsed = _parse_job_units(job, planned_by_id)
    stamp = job["updated_at"] or job["created_at"]
    if parsed is None:
        fact = CapabilityJobFact(
            job_type=str(job["job_type"]),
            status=str(job["status"]),
            pause_owner=job["pause_owner"],
            rationale_code=job["rationale_code"],
            failure_code=MALFORMED_JOB_FAILURE_CODE,
            updated_at=stamp,
            malformed=True,
        )
        for facts in _malformed_targets(
            job,
            bucket=bucket,
            planned_by_id=planned_by_id,
            translation_facts=translation_facts,
            section_vocab=section_vocab,
            section_grammar=section_grammar,
        ):
            facts.jobs.append(fact)
            _add_time(facts, stamp)
        return
    fact = CapabilityJobFact(
        job_type=str(job["job_type"]),
        status=str(job["status"]),
        pause_owner=job["pause_owner"],
        rationale_code=job["rationale_code"],
        failure_code=job["failure_code"],
        updated_at=stamp,
        captured_resume_ready=bool(job["captured_resume_ready"]),
    )
    for facts in _job_targets(
        parsed,
        bucket=bucket,
        unit_to_section=unit_to_section,
        translation_facts=translation_facts,
        section_vocab=section_vocab,
        section_grammar=section_grammar,
    ):
        facts.jobs.append(fact)
        _add_time(facts, stamp)
        if fact.status in _SUCCESS_JOB_STATUSES:
            facts.completed_ids.update(parsed)


def _parse_job_units(
    job: asyncpg.Record,
    planned_by_id: dict[str, AnalysisSection],
) -> tuple[str, ...] | None:
    payload = job["input_json"]
    if payload is not None and not isinstance(payload, dict):
        return None
    payload = payload or {}
    job_type = str(job["job_type"])
    target_key = str(job["target_key"] or "")
    origin = payload.get("request_origin")
    section_id = payload.get("analysis_section_id")
    if origin == ANALYSIS_SECTION_REQUEST_ORIGIN or isinstance(section_id, str):
        if not isinstance(section_id, str) or section_id not in planned_by_id:
            return None
        if target_key and target_key != section_id:
            return None
        raw_ids = payload.get("target_unit_ids")
        if raw_ids is None:
            return planned_by_id[section_id].target_unit_ids
        if not isinstance(raw_ids, list) or not all(
            isinstance(item, str) and item for item in raw_ids
        ):
            return None
        allowed = set(planned_by_id[section_id].target_unit_ids)
        if any(item not in allowed for item in raw_ids):
            return None
        return tuple(raw_ids)
    if job_type in _BATCH_JOB_TYPES or str(job["target_type"] or "") == "unit_range":
        raw_ids = payload.get("target_unit_ids")
        if not isinstance(raw_ids, list) or not raw_ids:
            return None
        if not all(isinstance(item, str) and item for item in raw_ids):
            return None
        return tuple(raw_ids)
    if job_type in _PER_UNIT_JOB_TYPES or str(job["target_type"] or "") == "unit":
        return (target_key,) if target_key else None
    return None


def _bucket_for_job(job_type: str) -> CapabilityBucket | None:
    if job_type in _TRANSLATION_JOB_TYPES:
        return "translation"
    if job_type in _VOCABULARY_JOB_TYPES:
        return "vocabulary"
    if job_type in _GRAMMAR_JOB_TYPES:
        return "grammar"
    return None


def _job_targets(
    unit_ids: tuple[str, ...],
    *,
    bucket: CapabilityBucket,
    unit_to_section: dict[str, int],
    translation_facts: _CapabilityFacts,
    section_vocab: list[_CapabilityFacts],
    section_grammar: list[_CapabilityFacts],
) -> list[_CapabilityFacts]:
    if bucket == "translation":
        return [translation_facts]
    source = section_vocab if bucket == "vocabulary" else section_grammar
    indexes = sorted(
        {unit_to_section[unit_id] for unit_id in unit_ids if unit_id in unit_to_section}
    )
    return [source[index] for index in indexes]


def _malformed_targets(
    job: asyncpg.Record,
    *,
    bucket: CapabilityBucket,
    planned_by_id: dict[str, AnalysisSection],
    translation_facts: _CapabilityFacts,
    section_vocab: list[_CapabilityFacts],
    section_grammar: list[_CapabilityFacts],
) -> list[_CapabilityFacts]:
    if bucket == "translation":
        return [translation_facts]
    payload = job["input_json"] if isinstance(job["input_json"], dict) else {}
    section_id = payload.get("analysis_section_id") or job["target_key"]
    source = section_vocab if bucket == "vocabulary" else section_grammar
    if isinstance(section_id, str) and section_id in planned_by_id:
        return [source[planned_by_id[section_id].order_index]]
    return []


def _reduce_section_status(
    vocabulary: ReaderAnalysisCapabilityStatus,
    grammar: ReaderAnalysisCapabilityStatus,
) -> ReaderAnalysisCapabilityStatus:
    pair = {vocabulary, grammar}
    if "processing" in pair:
        return "processing"
    if "paused_quota" in pair:
        return "paused_quota"
    if "queued" in pair:
        return "queued"
    if pair == {"completed"}:
        return "completed"
    if "partial" in pair or ("completed" in pair and pair != {"completed"}):
        return "partial"
    if "failed" in pair:
        return "failed"
    return "not_started"


def _can_start(
    *,
    section_status: ReaderAnalysisCapabilityStatus,
    is_first: bool,
    first_section_status: ReaderAnalysisCapabilityStatus,
    nonterminal_translation: bool,
) -> bool:
    if nonterminal_translation:
        return False
    if section_status in _ACTIVE_SECTION_STATUSES or section_status == "paused_quota":
        return False
    if section_status == "completed":
        return False
    if is_first:
        return True
    return first_section_status in _FIRST_SECTION_READY_STATUSES


def _reduce_overall(
    *,
    mode: ReaderAnalysisMode,
    translation_status: ReaderAnalysisCapabilityStatus,
    section_statuses: Sequence[ReaderAnalysisCapabilityStatus],
    any_can_start: bool,
    translation_jobs: Sequence[CapabilityJobFact],
) -> ReaderAnalysisOverallStatus:
    statuses = [translation_status, *section_statuses]
    if "processing" in statuses:
        return "processing"
    if translation_status == "paused_quota" or any(
        _is_quota_pause(job) or _is_budget_exhausted(job) for job in translation_jobs
    ):
        return "paused_quota"
    if any(_is_unrecoverable_pause(job) for job in translation_jobs):
        return "failed"
    if "paused_quota" in statuses:
        return "paused_quota"
    if "queued" in statuses:
        return "queued"
    if translation_status == "completed" and all(
        status == "completed" for status in section_statuses
    ):
        return "completed"
    if (
        mode == "segmented_on_demand"
        and not _is_active_status(translation_status)
        and not any(status in _ACTIVE_SECTION_STATUSES for status in section_statuses)
        and any_can_start
    ):
        return "waiting_user"
    has_complete = any(status in {"completed", "partial"} for status in statuses)
    has_fail = "failed" in statuses
    if has_complete:
        return "partial"
    if has_fail:
        return "failed"
    return "queued"


def _needs_user_action(
    *,
    overall: ReaderAnalysisOverallStatus,
    capability_statuses: Sequence[ReaderAnalysisCapabilityStatus],
    jobs: Sequence[CapabilityJobFact],
) -> bool:
    if overall == "waiting_user":
        return True
    if "paused_quota" in capability_statuses:
        return True
    return any(_is_unrecoverable_pause(job) for job in jobs)


def _is_active_status(status: ReaderAnalysisCapabilityStatus) -> bool:
    return status in _ACTIVE_SECTION_STATUSES


def _is_quota_pause(job: CapabilityJobFact) -> bool:
    return job.status == "paused" and job.pause_owner == QUOTA_PAUSE_OWNER


def _is_budget_exhausted(job: CapabilityJobFact) -> bool:
    return job.failure_code == BUDGET_EXHAUSTED_CODE and (
        job.status in _FAILURE_JOB_STATUSES or job.status == "paused"
    )


def _is_unrecoverable_pause(job: CapabilityJobFact) -> bool:
    return (
        job.status == "paused"
        and not job.malformed
        and not job.captured_resume_ready
        and not _is_quota_pause(job)
    )


def _stable_failure_code(job: CapabilityJobFact) -> str:
    if job.failure_code:
        return str(job.failure_code)
    if job.rationale_code:
        return str(job.rationale_code)
    if job.status in _FAILURE_JOB_STATUSES:
        return ANALYSIS_JOB_FAILED
    if job.status == "paused":
        return ANALYSIS_JOB_PAUSED
    return ANALYSIS_JOB_FAILED


def _latest_failure_code(jobs: list[CapabilityJobFact]) -> str:
    latest = max(jobs, key=lambda job: job.updated_at or datetime.min)
    return latest.failure_code or _stable_failure_code(latest)


def _section_failure_code(
    status: ReaderAnalysisCapabilityStatus,
    *codes: str | None,
) -> str | None:
    if status not in _FAILURE_VISIBLE_STATUSES:
        return None
    for code in codes:
        if code:
            return code
    return None


def _add_time(facts: _CapabilityFacts, stamp: datetime | None) -> None:
    if stamp is not None:
        facts.timestamps.append(stamp)


def _max_time(stamps: list[datetime]) -> datetime | None:
    return max(stamps) if stamps else None
