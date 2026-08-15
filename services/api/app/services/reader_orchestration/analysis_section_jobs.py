"""Automatic first-section job contract and translation-terminal gate.

Shared by bootstrap, claim, and worker-loop scan. Not a second scheduler.
"""

from __future__ import annotations

ANALYSIS_SECTION_REQUEST_ORIGIN = "automatic_analysis_section_v1"
VOCABULARY_ANALYSIS_SECTION_FINGERPRINT = "vocabulary_analysis_section_v1"
GRAMMAR_ANALYSIS_SECTION_FINGERPRINT = "grammar_analysis_section_v1"
VOCABULARY_ANALYSIS_SECTION_POLICY_VERSION = "reader_vocabulary_analysis_section_v1"
GRAMMAR_ANALYSIS_SECTION_POLICY_VERSION = "reader_grammar_analysis_section_v1"

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
