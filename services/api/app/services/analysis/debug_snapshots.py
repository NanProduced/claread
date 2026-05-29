from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any
from uuid import UUID

from app.config.settings import get_settings
from app.database import connection as db_connection
from app.database.json_compat import jsonb_param
from app.schemas.internal.academic_normalized import AcademicNormalizedResult
from app.schemas.internal.analysis import PreparedInput
from app.schemas.internal.normalized import DropLogEntry, NormalizedAnnotationResult
from app.workflow.academic_workflow import ACADEMIC_WORKFLOW_NAME, ACADEMIC_WORKFLOW_VERSION
from app.workflow.analyze_nodes import WORKFLOW_NAME, WORKFLOW_VERSION


def _model_to_dict(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _coerce_prepared_input(result: dict[str, Any] | None) -> PreparedInput | None:
    if not result:
        return None
    prepared_input = result.get("prepared_input")
    if isinstance(prepared_input, PreparedInput):
        return prepared_input
    if isinstance(prepared_input, dict):
        return PreparedInput.model_validate(prepared_input)
    return None


def _coerce_learning_normalized(result: dict[str, Any] | None) -> NormalizedAnnotationResult | None:
    if not result:
        return None
    normalized = result.get("normalized_result")
    if isinstance(normalized, NormalizedAnnotationResult):
        return normalized
    if isinstance(normalized, dict):
        return NormalizedAnnotationResult.model_validate(normalized)
    return None


def _coerce_academic_normalized(result: dict[str, Any] | None) -> AcademicNormalizedResult | None:
    if not result:
        return None
    normalized = result.get("academic_normalized_result")
    if isinstance(normalized, AcademicNormalizedResult):
        return normalized
    if isinstance(normalized, dict):
        return AcademicNormalizedResult.model_validate(normalized)
    return None


def _coerce_drop_log(result: dict[str, Any] | None) -> list[DropLogEntry] | None:
    if not result:
        return None
    raw_drop_log = result.get("drop_log")
    if raw_drop_log is None:
        return None
    drop_log: list[DropLogEntry] = []
    for entry in raw_drop_log:
        if isinstance(entry, DropLogEntry):
            drop_log.append(entry)
        elif isinstance(entry, dict):
            drop_log.append(DropLogEntry.model_validate(entry))
    return drop_log


def _source_text_hash(source_text: str) -> str:
    return sha256(source_text.strip().encode("utf-8")).hexdigest()[:16]


def resolve_workflow_identity(result: dict[str, Any] | None) -> tuple[str, str]:
    if result:
        plan = result.get("goal_execution_plan")
        topology_mode = getattr(plan, "topology_mode", None)
        if topology_mode is None and isinstance(plan, dict):
            topology_mode = plan.get("topology_mode")
        if topology_mode == "academic":
            return ACADEMIC_WORKFLOW_NAME, ACADEMIC_WORKFLOW_VERSION
    return WORKFLOW_NAME, WORKFLOW_VERSION


def build_preprocess_summary(
    source_text: str,
    result: dict[str, Any] | None,
) -> dict[str, Any] | None:
    prepared_input = _coerce_prepared_input(result)
    if prepared_input is None:
        return None
    return {
        "source_text_hash": _source_text_hash(source_text),
        "text_type": prepared_input.text_type,
        "fast_path": prepared_input.fast_path,
        "language_detected": prepared_input.language_detected,
        "english_ratio": prepared_input.english_ratio,
        "noise_ratio": prepared_input.noise_ratio,
        "sanitize": {
            "action_count": len(prepared_input.sanitize_report.actions),
            "removed_segment_count": prepared_input.sanitize_report.removed_segment_count,
            "actions": list(prepared_input.sanitize_report.actions),
        },
    }


def build_normalize_summary(result: dict[str, Any] | None) -> dict[str, Any] | None:
    learning = _coerce_learning_normalized(result)
    if learning is not None:
        warnings = result.get("warnings") if result else []
        warning_codes = sorted(
            {
                str(code)
                for warning in warnings
                for code in [
                    warning.get("code") if isinstance(warning, dict) else getattr(warning, "code", None)
                ]
                if code
            }
        )
        quality_drop_count = len(
            [entry for entry in learning.drop_log if entry.drop_stage != "density_control"]
        )
        repair_request = result.get("repair_request") if result else None
        repair_attempted = bool(repair_request)
        repair_succeeded = (
            bool(repair_request.get("repaired"))
            if isinstance(repair_request, dict) and repair_request.get("repaired") is not None
            else None
        )
        return {
            "mode": "learning",
            "annotation_count": len(learning.annotations),
            "translation_count": len(learning.sentence_translations),
            "warning_codes": warning_codes,
            "quality_drop_count": quality_drop_count,
            "total_drop_count": len(learning.drop_log),
            "repair_attempted": repair_attempted,
            "repair_succeeded": repair_succeeded,
        }

    academic = _coerce_academic_normalized(result)
    if academic is not None:
        return {
            "mode": "academic",
            "term_annotation_count": len(academic.term_annotations),
            "translation_count": len(academic.sentence_translations),
            "logic_note_count": len(academic.logic_notes),
            "interpretation_note_count": len(academic.interpretation_notes),
            "paragraph_role_count": len(academic.paragraph_roles),
            "content_summary_present": academic.content_summary is not None,
        }

    return None


def build_drop_log_summary(result: dict[str, Any] | None) -> dict[str, Any] | None:
    drop_log = _coerce_drop_log(result)
    if drop_log is None:
        return None

    quality_drop_count = len([entry for entry in drop_log if entry.drop_stage != "density_control"])
    by_stage = Counter(entry.drop_stage for entry in drop_log)
    by_source_agent = Counter(entry.source_agent for entry in drop_log)
    by_annotation_type = Counter(entry.annotation_type for entry in drop_log)
    by_reason = Counter(entry.drop_reason for entry in drop_log)

    return {
        "available": True,
        "total_drop_count": len(drop_log),
        "quality_drop_count": quality_drop_count,
        "by_stage": dict(sorted(by_stage.items())),
        "by_source_agent": dict(sorted(by_source_agent.items())),
        "by_annotation_type": dict(sorted(by_annotation_type.items())),
        "top_reasons": [
            {"reason": reason, "count": count}
            for reason, count in by_reason.most_common(5)
        ],
    }


def build_runtime_summary(
    usage_summary: dict[str, Any] | None,
    *,
    latency_ms: int,
    billed_points: int,
) -> dict[str, Any]:
    usage_summary = usage_summary or {}
    aggregate = usage_summary.get("aggregate") if isinstance(usage_summary.get("aggregate"), dict) else {}
    return {
        "usage_available": bool(usage_summary.get("available")),
        "per_agent": usage_summary.get("per_agent") if isinstance(usage_summary.get("per_agent"), dict) else {},
        "aggregate": {
            "input_tokens": aggregate.get("input_tokens"),
            "output_tokens": aggregate.get("output_tokens"),
            "total_tokens": aggregate.get("total_tokens"),
        },
        "billed_points": billed_points,
        "latency_ms": latency_ms,
    }


def build_academic_quality(result: dict[str, Any] | None) -> dict[str, Any] | None:
    academic = _coerce_academic_normalized(result)
    if academic is None:
        return None
    return {
        "quality_state": academic.quality_state,
        "quality_issues": list(academic.quality_issues),
        "paragraph_roles": [_model_to_dict(role) for role in academic.paragraph_roles],
    }


def build_trace_refs(
    *,
    request_id: str | None,
) -> dict[str, Any]:
    settings = get_settings()
    return {
        "request_id": request_id,
        "langsmith_enabled": settings.langsmith_enabled,
        "langsmith_project": settings.langsmith_project if settings.langsmith_enabled else None,
        "workflow_run_id": None,
        "trace_url": None,
    }


def build_debug_snapshot_payload(
    *,
    record_id: UUID,
    task_id: UUID,
    source_text: str,
    task_status: str,
    usage_summary: dict[str, Any] | None,
    latency_ms: int,
    billed_points: int,
    failure_code: str | None,
    failure_message: str | None,
    request_id: str | None,
    user_facing_state: str | None,
    result: dict[str, Any] | None,
    schema_version: str | None,
    prompt_version: str,
) -> dict[str, Any]:
    workflow_name, workflow_version = resolve_workflow_identity(result)
    return {
        "record_id": record_id,
        "task_id": task_id,
        "workflow_name": workflow_name,
        "workflow_version": workflow_version,
        "schema_version": schema_version,
        "prompt_version": prompt_version,
        "task_status": task_status,
        "user_facing_state": user_facing_state,
        "failure_code": failure_code,
        "failure_message": failure_message,
        "preprocess_summary_json": build_preprocess_summary(source_text, result),
        "normalize_summary_json": build_normalize_summary(result),
        "drop_log_summary_json": build_drop_log_summary(result),
        "runtime_summary_json": build_runtime_summary(
            usage_summary,
            latency_ms=latency_ms,
            billed_points=billed_points,
        ),
        "academic_quality_json": build_academic_quality(result),
        "few_shot_debug_json": result.get("few_shot_debug") if result else None,
        "rag_debug_json": result.get("rag_debug") if result else None,
        "trace_refs_json": build_trace_refs(request_id=request_id),
    }


async def upsert_debug_snapshot(snapshot: dict[str, Any]) -> None:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    now = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO analysis_debug_snapshots (
                record_id,
                task_id,
                workflow_name,
                workflow_version,
                schema_version,
                prompt_version,
                task_status,
                user_facing_state,
                failure_code,
                failure_message,
                preprocess_summary_json,
                normalize_summary_json,
                drop_log_summary_json,
                runtime_summary_json,
                academic_quality_json,
                few_shot_debug_json,
                rag_debug_json,
                trace_refs_json,
                created_at,
                updated_at
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                $11::jsonb, $12::jsonb, $13::jsonb, $14::jsonb,
                $15::jsonb, $16::jsonb, $17::jsonb, $18::jsonb, $19, $19
            )
            ON CONFLICT (task_id) DO UPDATE SET
                record_id = EXCLUDED.record_id,
                workflow_name = EXCLUDED.workflow_name,
                workflow_version = EXCLUDED.workflow_version,
                schema_version = EXCLUDED.schema_version,
                prompt_version = EXCLUDED.prompt_version,
                task_status = EXCLUDED.task_status,
                user_facing_state = EXCLUDED.user_facing_state,
                failure_code = EXCLUDED.failure_code,
                failure_message = EXCLUDED.failure_message,
                preprocess_summary_json = EXCLUDED.preprocess_summary_json,
                normalize_summary_json = EXCLUDED.normalize_summary_json,
                drop_log_summary_json = EXCLUDED.drop_log_summary_json,
                runtime_summary_json = EXCLUDED.runtime_summary_json,
                academic_quality_json = EXCLUDED.academic_quality_json,
                few_shot_debug_json = EXCLUDED.few_shot_debug_json,
                rag_debug_json = EXCLUDED.rag_debug_json,
                trace_refs_json = EXCLUDED.trace_refs_json,
                updated_at = EXCLUDED.updated_at
            """,
            snapshot["record_id"],
            snapshot["task_id"],
            snapshot["workflow_name"],
            snapshot["workflow_version"],
            snapshot["schema_version"],
            snapshot["prompt_version"],
            snapshot["task_status"],
            snapshot["user_facing_state"],
            snapshot["failure_code"],
            snapshot["failure_message"],
            jsonb_param(snapshot.get("preprocess_summary_json")),
            jsonb_param(snapshot.get("normalize_summary_json")),
            jsonb_param(snapshot.get("drop_log_summary_json")),
            jsonb_param(snapshot.get("runtime_summary_json")),
            jsonb_param(snapshot.get("academic_quality_json")),
            jsonb_param(snapshot.get("few_shot_debug_json")),
            jsonb_param(snapshot.get("rag_debug_json")),
            jsonb_param(snapshot.get("trace_refs_json")),
            now,
        )
