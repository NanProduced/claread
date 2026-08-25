"""Offline-only teaching v2 prototype contracts.

Since P-5B the canonical five-stage prompts and evidence contracts are
single-sourced in the shared stdlib-only teaching package; this module
re-exports them for existing evals callers.
"""

from __future__ import annotations

from app.services.daily_reader.teaching.prototype import (  # noqa: F401
    SEMANTIC_REVIEW_CONTRACTS,
    TRANSFER_CONTENT_REQUIREMENT_VALUES,
    TRANSFER_TASK_KIND_BY_ARTICLE_TYPE,
    build_blueprint_prompt,
    build_language_support_prompt,
    build_refinement_evidence,
    build_refinement_prompt,
    build_semantic_review_prompt,
    build_translation_prompt,
    derive_translation_unit_ids,
    make_review_evidence,
    run_prototype_dry_run,
    transfer_task_kind,
    validate_teaching_contract,
)
