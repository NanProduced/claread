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
3. **Test identifiers** under ``tests/`` — function, class and
   module-level assignment names must not embed task codes. String
   literals and comments stay exempt: fixture payloads, protocol
   values, persisted/artifact/migration versions and dataset/env
   identities are durable contracts and are never scanned here.

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

# Task-code signatures for test identifiers (P3 identifier governance).
# Family set aligned with the P3 Phase-1 audit: ``p<N>``/``r<N>``/
# ``t<N>``/``t<NN>[x]``/``d5``/``d6``/``a3``-``a5``/``i3``/``i4``/
# ``s<N>``/``round<N>``/``lp_r<N>``/``g0[x]`` in snake names, plus
# CamelCase ``T<NN>``/``A<3-5>``/``D<5-6>``/``R<N>``/``Round<N>``/
# ``I<3-4><X>`` tokens. Token-boundary lookarounds keep business
# words out: single-digit ``t<N>`` needs the underscore separator
# (``utf8``/``split1`` never match), identifier-initial ``i<3-4>``
# is caught only at a real identifier boundary (``i18n`` never
# matches), and ``Round<N>`` requires a digit so ``RoundRobin``
# passes. ``zplus``/``ZPlus`` are production domain words (see the
# production allowlist above) and are intentionally NOT part of
# this set.
_TEST_IDENTIFIER_TASK_CODE_RE = re.compile(
    r"_(?:d[56]|a[345]|t[0-9][0-9]?[a-z0-9]?|r[0-9]|p[0-9]|i[34]"
    r"|s[0-9]|round[0-9]|lp_r[0-9]|g0[0-9]?)"
    r"|(?<![A-Z0-9_])D[56](?![0-9])"
    r"|(?<![A-Z0-9_])A[345](?![0-9])"
    r"|(?<![A-Z0-9_])T[0-9][0-9][a-z0-9]?(?![0-9])"
    r"|(?<![A-Z0-9_])R[0-9](?![0-9])"
    r"|(?<![A-Za-z0-9_])i[34](?![0-9])"
    r"|(?<![A-Z0-9_])Round[0-9]"
    r"|(?<![A-Z0-9_])I[34][A-Z0-9]"
)

# Ratchet ceilings (GOVERNANCE-CLOSEOUT-R1): allowlist sizes must match
# exactly — an equality ratchet, so a shrunk allowlist can never grow
# back. Every governance rename lowers the ceiling in the same change.
TEST_FILE_ALLOWLIST_CEILING = 0
PRODUCTION_SYMBOL_ALLOWLIST_CEILING = 24
TEST_IDENTIFIER_ALLOWLIST_CEILING = 14


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

# Existing test identifiers with external contracts (P3 identifier
# governance; file:symbol, relative to services/api). Same ratchet
# rules as above. KEEP evidence per group:
# - ``R4_A3_*_ENV``: the env var names ``CLAREAD_R4_A3_*`` are the
#   real-LLM eval dataset/run identity consumed by evals/** harness
#   code (dataset id + run contract), not task history.
# - schema-health test names: they exercise the READER_D5/D6
#   production symbols allowlisted above, which are permanent schema
#   identity (migration/schema contract).
# - ``..._in_round0``: ``round0`` is the reasoning-projection round
#   index (domain term; cf. production ``advance_round``), not a task
#   code.
TASK_NUMBER_TEST_IDENTIFIER_ALLOWLIST: frozenset[str] = frozenset(
    {
        "tests/test_reader_record_ask_real_llm_eval.py:R4_A3_BBC_RECORD_ID_ENV",
        "tests/test_reader_record_ask_real_llm_eval.py:R4_A3_DATASET_DIR_ENV",
        "tests/test_reader_record_ask_real_llm_eval.py:R4_A3_MAX_REQUESTS_ENV",
        "tests/test_reader_record_ask_real_llm_eval.py:R4_A3_MAX_TOKENS_ENV",
        "tests/test_reader_record_ask_real_llm_eval.py:R4_A3_PRO_PROFILE_ENV",
        "tests/test_reader_record_ask_real_llm_eval.py:R4_A3_RUNS_DIR_ENV",
        "tests/test_reader_record_ask_real_llm_eval.py:R4_A3_RUN_ENV",
        "tests/test_reader_record_ask_real_llm_eval.py:R4_A3_THINKING_VIA_PROFILE_ENV",
        "tests/test_reader_orchestration_schema_health.py:test_check_schema_baseline_sql_covers_reader_d5_attribution_objects",
        "tests/test_reader_orchestration_schema_health.py:test_reader_d5_schema_health_passes_on_fresh_baseline",
        "tests/test_reader_orchestration_schema_health.py:test_reader_d5_schema_health_reports_drift_with_reset_guidance",
        "tests/test_reader_orchestration_schema_health.py:test_reader_schema_health_reports_missing_d6_anchor_column_with_0002_guidance",
        "tests/test_reader_orchestration_schema_health.py:test_reader_schema_health_reports_missing_d6_anchor_index",
        "tests/test_reader_record_ask_reasoning_projection.py:test_no_round0_subcap_allows_full_total_budget_in_round0",
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


def _test_identifier_hits() -> set[str]:
    hits: set[str] = set()
    for path in sorted(TESTS_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.relative_to(SERVICE_ROOT).as_posix()
        names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                names.append(node.name)
        for node in tree.body:
            if isinstance(node, ast.Assign):
                names.extend(t.id for t in node.targets if isinstance(t, ast.Name))
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names.append(node.target.id)
        hits.update(
            f"{rel}:{name}"
            for name in names
            if _TEST_IDENTIFIER_TASK_CODE_RE.search(name)
        )
    return hits


def test_test_identifiers_carry_no_task_codes() -> None:
    """Test identifiers must not embed task codes (ratchet).

    Only AST identifier names are scanned (functions, classes,
    module-level assignments). String literals and comments — fixture
    payloads, protocol values, persisted/artifact/migration versions,
    dataset/env identities — are exempt by design and out of scope.
    """
    actual = _test_identifier_hits()
    unlisted = actual - TASK_NUMBER_TEST_IDENTIFIER_ALLOWLIST
    assert not unlisted, (
        "test identifiers must not embed task codes; rename to a "
        f"business name instead of allowlisting: {sorted(unlisted)}"
    )
    stale = TASK_NUMBER_TEST_IDENTIFIER_ALLOWLIST - actual
    assert not stale, (
        "allowlist is a ratchet and only shrinks; remove stale entries: "
        f"{sorted(stale)}"
    )
    assert (
        len(TASK_NUMBER_TEST_IDENTIFIER_ALLOWLIST)
        == TEST_IDENTIFIER_ALLOWLIST_CEILING
    ), (
        "test-identifier allowlist size must equal its ratchet ceiling; "
        "when renaming stock, remove the entry AND lower the ceiling together"
    )


def test_task_code_pattern_flags_synthetic_task_code() -> None:
    """A synthetic task-coded identifier must be caught (backflow proof)."""
    tree = ast.parse("async def test_r6_stale_stream_reconcile():\n    pass\n")
    name = tree.body[0].name
    assert _TEST_IDENTIFIER_TASK_CODE_RE.search(name), name
    # Blind-spot forms closed by the P3 follow-up: single-digit
    # ``t<N>`` token, identifier-initial ``i<3-4>`` fixture names,
    # CamelCase ``Round<N>`` and ``I<3-4><X>`` class tokens.
    for name in (
        "test_t5_synthetic_single_digit_token",
        "i4x_env",
        "TestRound2SyntheticGates",
        "TestSyntheticI3ZSections",
    ):
        assert _TEST_IDENTIFIER_TASK_CODE_RE.search(name), name


def test_task_code_pattern_passes_business_names() -> None:
    """Business identifiers and domain terms must not be flagged."""
    for name in (
        "test_budget_stop_report_counts",
        "test_spans_order_consistent_with_reading_order",
        "_make_write_chunk",
        "TestFreezePersistenceSqlOrder",
        "advance_round",
        "zplus_service",
        "layer1_fnv1a32",
        "test_utf8_encode_roundtrip",
        "i18n_labels",
    ):
        assert not _TEST_IDENTIFIER_TASK_CODE_RE.search(name), name
