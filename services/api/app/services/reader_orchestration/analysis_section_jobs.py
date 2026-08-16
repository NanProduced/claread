"""Automatic first-section job contract and translation-terminal gate.

Shared by bootstrap, claim, and worker-loop scan. Not a second scheduler.
"""

from __future__ import annotations

from app.services.reader_orchestration.analysis_section_plan import (
    ANALYSIS_SECTION_PLAN_VERSION,
)

ANALYSIS_SECTION_REQUEST_ORIGIN = "automatic_analysis_section_v1"
USER_EXPLICIT_ANALYSIS_SECTION_ORIGIN = "user_explicit_analysis_section"
VOCABULARY_ANALYSIS_SECTION_FINGERPRINT = "vocabulary_analysis_section_v1"
GRAMMAR_ANALYSIS_SECTION_FINGERPRINT = "grammar_analysis_section_v1"
VOCABULARY_ANALYSIS_SECTION_POLICY_VERSION = "reader_vocabulary_analysis_section_v1"
GRAMMAR_ANALYSIS_SECTION_POLICY_VERSION = "reader_grammar_analysis_section_v1"
VOCABULARY_ANALYSIS_SECTION_JOB_TYPE = "build_vocabulary_layer_article"
GRAMMAR_ANALYSIS_SECTION_JOB_TYPE = "build_grammar_bundle"
ANALYSIS_SECTION_ORIGINS = frozenset(
    {
        ANALYSIS_SECTION_REQUEST_ORIGIN,
        USER_EXPLICIT_ANALYSIS_SECTION_ORIGIN,
    }
)
_MODEL_EXECUTION_PAUSE_RATIONALES = frozenset(
    {
        "model_execution_captured_resume_required",
        "model_execution_ambiguous",
        "model_execution_receipt_invalid",
    }
)
_USER_RESUMABLE_PAUSE_OWNERS = frozenset({"quota", "user"})
# Schema CHECK on reader_events.event_type is closed; no migration this task.
ANALYSIS_PROGRESS_CHANGED_EVENT = "record_state_changed"

TRANSLATION_TERMINAL_GATE_JOB_TYPES: tuple[str, ...] = (
    "translate_unit",
    "translate_article",
)
TRANSLATION_NON_TERMINAL_STATUSES: tuple[str, ...] = (
    "queued",
    "claimed",
    "retry_later",
    "paused",
)


def is_resumable_user_paused_analysis_job(
    *,
    job_type: str,
    operation_fingerprint: str,
    request_origin: object,
    plan_version: object,
    status: object,
    pause_owner: object,
    rationale_code: object,
    failure_class: object,
    failure_code: object,
) -> bool:
    """True only for a current, non-captured analysis-section pause."""
    if status != "paused":
        return False
    if request_origin not in ANALYSIS_SECTION_ORIGINS:
        return False
    if plan_version != ANALYSIS_SECTION_PLAN_VERSION:
        return False
    if failure_class == "model_execution":
        return False
    if rationale_code in _MODEL_EXECUTION_PAUSE_RATIONALES:
        return False
    fingerprint = str(operation_fingerprint or "")
    if job_type == VOCABULARY_ANALYSIS_SECTION_JOB_TYPE:
        if not fingerprint.startswith(VOCABULARY_ANALYSIS_SECTION_FINGERPRINT):
            return False
    elif job_type == GRAMMAR_ANALYSIS_SECTION_JOB_TYPE:
        if not fingerprint.startswith(GRAMMAR_ANALYSIS_SECTION_FINGERPRINT):
            return False
    else:
        return False
    return (
        pause_owner in _USER_RESUMABLE_PAUSE_OWNERS
        or failure_code == "budget_exhausted"
    )


def sql_trusted_explicit_analysis_runnable(*, job_alias: str) -> str:
    """coverage_complete scanner: only user-explicit analysis-section jobs."""
    return f"""
    (
      ({job_alias}.input_json->>'request_origin')
        = '{USER_EXPLICIT_ANALYSIS_SECTION_ORIGIN}'
      AND ({job_alias}.input_json->>'analysis_section_plan_version')
        = '{ANALYSIS_SECTION_PLAN_VERSION}'
      AND {job_alias}.job_type IN (
        'build_vocabulary_layer_article',
        'build_grammar_bundle'
      )
      AND (
        starts_with(
          {job_alias}.operation_fingerprint,
          '{VOCABULARY_ANALYSIS_SECTION_FINGERPRINT}'
        )
        OR starts_with(
          {job_alias}.operation_fingerprint,
          '{GRAMMAR_ANALYSIS_SECTION_FINGERPRINT}'
        )
      )
    )
    """


def sql_blocked_by_active_translation(*, job_alias: str) -> str:
    """SQL predicate: analysis-section job waiting on non-terminal translation."""
    job_types = ", ".join(
        f"'{job_type}'" for job_type in TRANSLATION_TERMINAL_GATE_JOB_TYPES
    )
    statuses = ", ".join(
        f"'{status}'" for status in TRANSLATION_NON_TERMINAL_STATUSES
    )
    return f"""
    (
      COALESCE(
        ({job_alias}.input_json->>'requires_translation_terminal'),
        'false'
      ) = 'true'
      AND EXISTS (
        SELECT 1
        FROM reader_jobs AS translation_dep
        WHERE translation_dep.reading_record_id = {job_alias}.reading_record_id
          AND translation_dep.base_id = {job_alias}.base_id
          AND translation_dep.expected_generation = {job_alias}.expected_generation
          AND translation_dep.job_type IN ({job_types})
          AND translation_dep.status IN ({statuses})
      )
    )
    """
