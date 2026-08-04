# task-history: TEST-GOVERNANCE-FOUNDATION-LONG-R1
"""Naming governance guard for services/api.

Task numbers (``d6_i4b``, ``t58a``, ``round20``, ``r0``, ``lp_r4``, ...)
are historical tracking metadata, not business identity. This guard
prevents their *backflow* into:

1. **New test file names** under ``tests/`` — existing stock is captured
   in ``TASK_NUMBER_TEST_FILE_ALLOWLIST`` below. The allowlist is a
   ratchet: entries may only be REMOVED (when a file is renamed to a
   business name or deleted); adding new entries fails this test.
2. **Production symbols** under ``app/`` — AST identifier names
   (functions, classes, assignment targets) must not embed task numbers.
   String literals are intentionally exempt: persisted identities such
   as protocol values, migration versions, ``execution_version`` and
   workflow versions are durable contracts, not naming drift.

This test is pure filesystem/AST — no DB, no network, no LLM.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.chain_infra,
    pytest.mark.seam_pure_unit,
    pytest.mark.life_permanent_regression,
]

SERVICE_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = SERVICE_ROOT / "tests"
APP_DIR = SERVICE_ROOT / "app"

# Task-number signature in underscore-separated names (audit 2026-07-23 §1.1-10).
# Business words are excluded: ``round`` must carry a digit task suffix
# (``round20``) so domain terms like ``advance_round`` do not match, and
# ``lp`` is only matched as the ``_lp_r<N>`` task form (plain ``_lp_``
# is the length-prefixed encoding in section_identity, a business term).
_TASK_NUMBER_NAME_RE = re.compile(
    r"_(?:d[56]_[a-z0-9]|a[345]_[a-z0-9]|t5[0-9][a-z0-9]?|t6[0-9][a-z0-9]?|"
    r"r[0-9][a-z0-9._]*|p[0-9][a-z0-9]*|s[0-9][a-z0-9]*|"
    r"round[0-9]+|lp_r[0-9])"
)

# CamelCase / UPPER_SNAKE task codes (R1 closeout): ``ReaderD5SchemaHealthReport``,
# ``READER_D5_*`` / ``READER_D6_*``, ``ZPlus*``. Digit-token boundary rules keep
# business words out (``D5``/``D6`` must not sit inside a longer digit run).
# Persisted identities stay exempt because only AST identifiers are scanned;
# string literals (protocol values, migration versions, ``execution_version``,
# workflow versions) never reach these matchers.
_TASK_CODE_IDENTIFIER_RE = re.compile(
    r"(?<![A-Z0-9])D[56](?![0-9])|(?<![A-Za-z0-9])ZPlus|(?<![A-Za-z0-9])zplus"
)

# Ratchet ceilings (GOVERNANCE-CLOSEOUT-R1): allowlist sizes must match
# exactly — an equality ratchet, so a shrunk allowlist can never grow
# back. Every governance rename lowers the ceiling in the same change.
TEST_FILE_ALLOWLIST_CEILING = 77
PRODUCTION_SYMBOL_ALLOWLIST_CEILING = 24


def _name_has_task_number(name: str) -> bool:
    return bool(_TASK_NUMBER_NAME_RE.search("_" + name)) or bool(
        _TASK_CODE_IDENTIFIER_RE.search(name)
    )

# Existing stock of task-numbered test files (relative to services/api).
# RATCHET: only shrink this list. Renamed/deleted files must have their
# entry removed in the same change; new task-numbered file names are
# forbidden and must be renamed to business names instead.
TASK_NUMBER_TEST_FILE_ALLOWLIST: frozenset[str] = frozenset(
    {
        "tests/services/reader_record_ask/thread_memory/test_r1a_integration.py",
        "tests/test_a4_parser_result_sharing_diagnostics.py",
        "tests/test_a5_stable_block_unit_classification.py",
        "tests/test_ask_retry_contract_r5_db_integration.py",
        "tests/test_ask_retry_contract_r6_db_integration.py",
        "tests/test_ask_retry_contract_r6.py",
        "tests/test_ask_retry_contract_r7.py",
        "tests/test_ask_retry_contract_r8.py",
        "tests/test_ask_submission_retry_r1_db_integration.py",
        "tests/test_cutover_api_p_closeout_r2.py",
        "tests/test_d6_a0_static_boundary.py",
        "tests/test_d6_a5_dual_contract_spike.py",
        "tests/test_d6_i1_stable_document_blocks.py",
        "tests/test_d6_i2_candidate_document_freeze_plan.py",
        "tests/test_d6_i2_stable_document_freeze_persistence.py",
        "tests/test_d6_i2d_candidate_document_confirm_application_service.py",
        "tests/test_d6_i2d_candidate_document_confirm_route.py",
        "tests/test_d6_i2d_candidate_document_confirm_service.py",
        "tests/test_d6_i2e_stable_document_query_service.py",
        "tests/test_d6_i2e_stable_document_route.py",
        "tests/test_d6_i3a_input_suitability_gate.py",
        "tests/test_d6_i3b_input_document_normalizer.py",
        "tests/test_d6_i3c_stable_ready_input_application_service.py",
        "tests/test_d6_i3d_stable_ready_input_route.py",
        "tests/test_d6_i3e_candidate_document_creation_service.py",
        "tests/test_d6_i3f_unified_input_submit_route.py",
        "tests/test_d6_i3g_source_artifact_service.py",
        "tests/test_d6_i3h_source_artifact_upload_init_route.py",
        "tests/test_d6_i3i_source_artifact_upload_complete_route.py",
        "tests/test_d6_i3j_artifact_input_application_service.py",
        "tests/test_d6_i3j_artifact_input_route.py",
        "tests/test_d6_i3l_artifact_extraction_worker.py",
        "tests/test_d6_i3m_text_artifact_extraction_provider.py",
        "tests/test_d6_i3n_extracted_artifact_materialization_service.py",
        "tests/test_d6_i3o_materialization_job_runtime.py",
        "tests/test_d6_i3p_artifact_pipeline_worker_service.py",
        "tests/test_d6_i3q_oss_artifact_io.py",
        "tests/test_d6_i3r_artifact_pipeline_worker_entry.py",
        "tests/test_d6_i3s_pdf_artifact_extraction_provider.py",
        "tests/test_d6_i3t_ocr_artifact_extraction_provider.py",
        "tests/test_d6_i3u_qwen_ocr_adapter.py",
        "tests/test_d6_i3v_artifact_input_status_query.py",
        "tests/test_d6_i3v_artifact_pipeline_status_route.py",
        "tests/test_d6_i4a_article_rag_index_plan.py",
        "tests/test_d6_i4b_article_rag_index_bootstrap.py",
        "tests/test_d6_i4c_article_rag_index_worker.py",
        "tests/test_d6_i4d_article_rag_provider_adapters.py",
        "tests/test_d6_i4e_article_rag_retrieval_service.py",
        "tests/test_d6_i4e_article_rag_vector_search.py",
        "tests/test_d6_i4f_article_rag_context_service.py",
        "tests/test_d6_i4g_article_rag_ask_context_composer.py",
        "tests/test_d6_i4h_article_rag_ask_context_resolver.py",
        "tests/test_d6_i4i_article_rag_ask_prompt_attachment.py",
        "tests/test_d6_i4j_article_rag_ask_integration_adapter.py",
        "tests/test_d6_i4k_article_rag_ask_prompt_section.py",
        "tests/test_d6_i4l_article_rag_ask_runtime_adapter.py",
        "tests/test_d6_i4m_article_rag_ask_prompt_assembly.py",
        "tests/test_d6_i4n_article_rag_ask_context_provider.py",
        "tests/test_d6_i4o_article_rag_ask_prompt_bridge.py",
        "tests/test_d6_i4s_article_rag_index_lifecycle_service.py",
        "tests/test_d6_i4t_article_rag_index_lifecycle_route.py",
        "tests/test_d6_i4u_article_rag_index_worker_entry.py",
        "tests/test_d6_i4v_article_rag_auto_ensure_service.py",
        "tests/test_d6_i4w_article_rag_service_e2e_smoke.py",
        "tests/test_d6_i4y_article_rag_operational_readiness.py",
        "tests/test_d6_i4z_article_rag_local_dry_run.py",
        "tests/test_p2b_r1_1_zplus_strict_fence_rollback.py",
        "tests/test_p2b_section_translation_semantic_fence.py",
        "tests/test_reader_parse_eval_r1.py",
        "tests/test_reader_record_ask_a5_7_production_wiring.py",
        "tests/test_reader_section_translation_t56b.py",
        "tests/test_reader_section_translation_t56c.py",
        "tests/test_reader_semantic_outline_t57.py",
        "tests/test_reader_semantic_outline_t58a_registration.py",
        "tests/test_reader_semantic_outline_t58b_adapter.py",
        "tests/test_reader_semantic_outline_t58c_real_llm.py",
        "tests/test_semantic_mode_and_worker_fence_r2.py",
    }
)

# Existing production symbols embedding task numbers (file:symbol,
# relative to services/api). Same ratchet rules as above. Renaming a
# listed symbol requires removing its entry in the same change.
TASK_NUMBER_PRODUCTION_SYMBOL_ALLOWLIST: frozenset[str] = frozenset(
    {
        "app/services/reader_orchestration/job_bootstrap.py:ZPlusBootstrapService",
        "app/services/reader_orchestration/job_bootstrap.py:_bootstrap_grammar_jobs_or_zplus",
        "app/services/reader_orchestration/job_bootstrap.py:use_zplus_grammar_path",
        "app/services/reader_orchestration/job_bootstrap.py:zplus_service",
        "app/services/reader_orchestration/schema_health.py:READER_D5_REQUIRED_COLUMNS",
        "app/services/reader_orchestration/schema_health.py:READER_D5_REQUIRED_CONSTRAINTS",
        "app/services/reader_orchestration/schema_health.py:READER_D5_REQUIRED_INDEXES",
        "app/services/reader_orchestration/schema_health.py:READER_D6_ANCHOR_COLUMNS",
        "app/services/reader_orchestration/schema_health.py:READER_D6_REQUIRED_CHECK_CONSTRAINT_SNIPPETS",
        "app/services/reader_orchestration/schema_health.py:READER_D6_REQUIRED_COLUMNS",
        "app/services/reader_orchestration/schema_health.py:READER_D6_REQUIRED_INDEXES",
        "app/services/reader_orchestration/schema_health.py:READER_D6_REQUIRED_NULLABLE_COLUMNS",
        "app/services/reader_orchestration/schema_health.py:ReaderD5SchemaHealthReport",
        "app/services/reader_orchestration/schema_health.py:_has_reader_d5_schema_drift",
        "app/services/reader_orchestration/schema_health.py:_has_reader_d6_schema_drift",
        "app/services/reader_orchestration/schema_health.py:check_reader_d5_schema_health",
        "app/services/reader_orchestration/schema_health.py:d5_constraint_names",
        "app/services/reader_orchestration/schema_health.py:d5_table_prefixes",
        "app/services/reader_orchestration/schema_health.py:d6_constraint_names",
        "app/services/reader_orchestration/schema_health.py:d6_table_prefixes",
        "app/services/reader_orchestration/schema_health.py:format_reader_d5_schema_health_failure",
        "app/services/reader_orchestration/zplus_bootstrap.py:ZPlusBootstrapResult",
        "app/services/reader_orchestration/zplus_bootstrap.py:ZPlusBootstrapService",
        "app/services/reader_record_ask/production_stream.py:_sync_submission_terminal_r6",
    }
)


def _test_file_relpaths() -> list[str]:
    return sorted(
        path.relative_to(SERVICE_ROOT).as_posix()
        for path in TESTS_DIR.rglob("*.py")
        if path.name != "__init__.py"
    )


def test_new_test_file_names_carry_no_task_numbers() -> None:
    """Task-numbered test file names are legacy stock only (ratchet)."""
    actual = {
        rel for rel in _test_file_relpaths() if _TASK_NUMBER_NAME_RE.search(Path(rel).name)
    }
    unlisted = actual - TASK_NUMBER_TEST_FILE_ALLOWLIST
    assert not unlisted, (
        "new task-numbered test file names are forbidden; rename to a "
        f"business name instead of allowlisting: {sorted(unlisted)}"
    )
    stale = TASK_NUMBER_TEST_FILE_ALLOWLIST - actual
    assert not stale, (
        "allowlist is a ratchet and only shrinks; these entries no longer "
        f"match an existing task-numbered file, remove them: {sorted(stale)}"
    )
    assert len(TASK_NUMBER_TEST_FILE_ALLOWLIST) == TEST_FILE_ALLOWLIST_CEILING, (
        "test-file allowlist size must equal its ratchet ceiling; when "
        "renaming stock, remove the entry AND lower the ceiling together"
    )


def _production_symbol_hits() -> set[str]:
    hits: set[str] = set()
    for path in sorted(APP_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.relative_to(SERVICE_ROOT).as_posix()
        names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                names.append(node.name)
            elif isinstance(node, ast.Assign | ast.AnnAssign):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                names.extend(t.id for t in targets if isinstance(t, ast.Name))
        names.extend(
            alias.asname or alias.name
            for stmt in ast.walk(tree)
            if isinstance(stmt, ast.ImportFrom)
            for alias in stmt.names
        )
        hits.update(
            f"{rel}:{name}" for name in names if _name_has_task_number(name)
        )
    return hits


def test_production_symbols_carry_no_task_numbers() -> None:
    """Task numbers must not backflow into production identifier names.

    String literals are exempt on purpose: protocol values, migration
    versions, ``execution_version`` and workflow versions are persisted
    identities, not naming drift.
    """
    actual = _production_symbol_hits()
    unlisted = actual - TASK_NUMBER_PRODUCTION_SYMBOL_ALLOWLIST
    assert not unlisted, (
        "production symbols must not embed task numbers; rename the "
        f"symbol to a business name: {sorted(unlisted)}"
    )
    stale = TASK_NUMBER_PRODUCTION_SYMBOL_ALLOWLIST - actual
    assert not stale, (
        "allowlist is a ratchet and only shrinks; remove stale entries: "
        f"{sorted(stale)}"
    )
    assert (
        len(TASK_NUMBER_PRODUCTION_SYMBOL_ALLOWLIST)
        == PRODUCTION_SYMBOL_ALLOWLIST_CEILING
    ), (
        "production-symbol allowlist size must equal its ratchet ceiling; "
        "when renaming stock, remove the entry AND lower the ceiling together"
    )
