# task-history: TEST-GOVERNANCE-FOUNDATION-LONG-R1 / TEST-GOVERNANCE-API-IDENTIFIERS-P3
"""Naming governance guard for services/api.

Task numbers (``d6_i4b``, ``t58a``, ``round20``, ``r0``, ``lp_r4``,
``a6``, ``l2``, ``R16``, ...) are historical tracking metadata, not
business identity. This guard prevents their *backflow* into:

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

Detection is token-based (stdlib AST + underscore/CamelCase token
split), not a growing pile of per-family regexes. A single letter +
1–3 digits forming a whole token is a task code unless it is on the
explicit business-term exclusion list.

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

# Whole-token business / technical identities that look like letter+digit
# task codes but are durable product, protocol or encoding terms. KEEP
# only what has standing evidence; do not grow this to paper over stock.
_BUSINESS_TOKEN_EXCLUSIONS: frozenset[str] = frozenset(
    {
        # product / protocol versions
        "v1",
        "v2",
        "v3",
        "v4",
        # representation-event formal classes + reading level
        "g1",
        "g2",
        "g3",
        "g5",
        # markdown / HTML heading levels used as domain terms
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        # common technical words
        "e2e",
        "i18n",
        "utf8",
        "utf16",
        "fnv1a32",
        "tecd3",
        "md5",
        "sha1",
        "sha256",
        "sha512",
    }
)

# Single letter + 1–3 digits as a whole underscore token (optional
# trailing letter for forms like ``t58a`` / ``i4x``). ``round<N>`` is
# always a task-stage index when it is a whole token (domain
# ``advance_round`` has no trailing digits and is therefore safe).
_SNAKE_TASK_TOKEN_RE = re.compile(r"^[A-Za-z]\d{1,3}[A-Za-z]?$")
_SNAKE_ROUND_TOKEN_RE = re.compile(r"^round\d+$", re.IGNORECASE)
# CamelCase letter+digit run: ``TestR16Feature`` → R16;
# ``TestT58aFeature`` → T58a; ``TestP2cFeature`` → P2c;
# ``TestG1RepresentationEvents`` → G1 (then excluded via KEEP).
# Optional trailing lowercase letter matches snake forms like ``t58a`` /
# ``i4x``. Applied only to mixed-case names so pure UPPER_SNAKE (``E2E``,
# ``SHA256``) is handled exclusively by snake tokens + KEEP.
_CAMEL_TASK_TOKEN_RE = re.compile(
    r"(?<![A-Z0-9])([A-Z]\d{1,3}[a-z]?)(?=[A-Z]|$)"
)
# ``TestRound2SyntheticGates`` — Round<N> after a lower-case letter or
# at a CamelCase word boundary.
_CAMEL_ROUND_TOKEN_RE = re.compile(r"(?:(?<=[a-z])|(?<=_)|^)Round(\d+)")

# Legacy production-side patterns retained for the app/ symbol scan so
# existing D5/D6/ZPlus stock continues to be measured against the
# production allowlist without adopting the broader test detector there.
_TASK_NUMBER_NAME_RE = re.compile(
    r"_(?:d[56]_[a-z0-9]|a[345]_[a-z0-9]|t5[0-9][a-z0-9]?|t6[0-9][a-z0-9]?|"
    r"r[0-9][a-z0-9._]*|p[0-9][a-z0-9]*|s[0-9][a-z0-9]*|"
    r"round[0-9]+|lp_r[0-9])"
)
_TASK_CODE_IDENTIFIER_RE = re.compile(
    r"(?<![A-Z0-9])D[56](?![0-9])|(?<![A-Za-z0-9])ZPlus|(?<![A-Za-z0-9])zplus"
)

# Ratchet ceilings (GOVERNANCE-CLOSEOUT-R1 / P3 closeout): allowlist
# sizes must match exactly — an equality ratchet, so a shrunk allowlist
# can never grow back.
TEST_FILE_ALLOWLIST_CEILING = 0
PRODUCTION_SYMBOL_ALLOWLIST_CEILING = 0
TEST_IDENTIFIER_ALLOWLIST_CEILING = 0


def _snake_tokens(name: str) -> list[str]:
    return [part for part in name.split("_") if part]


def _token_is_task_code(token: str) -> bool:
    lowered = token.lower()
    if lowered in _BUSINESS_TOKEN_EXCLUSIONS:
        return False
    if _SNAKE_TASK_TOKEN_RE.fullmatch(token):
        return True
    if _SNAKE_ROUND_TOKEN_RE.fullmatch(token):
        return True
    return False


def _camel_task_tokens(name: str) -> list[str]:
    hits: list[str] = []
    for match in _CAMEL_TASK_TOKEN_RE.finditer(name):
        token = match.group(1)
        if token.lower() not in _BUSINESS_TOKEN_EXCLUSIONS:
            hits.append(token)
    for match in _CAMEL_ROUND_TOKEN_RE.finditer(name):
        hits.append(f"Round{match.group(1)}")
    return hits


def identifier_has_task_code(name: str) -> bool:
    """True when ``name`` embeds a task-code token.

    Used for both test file stems and test AST identifiers. Production
    symbols keep the narrower legacy matcher via ``_name_has_task_number``.
    """
    if any(_token_is_task_code(token) for token in _snake_tokens(name)):
        return True
    # CamelCase scan only for mixed-case names. Pure UPPER_SNAKE
    # (``ZPLUS_E2E_ARTICLE_TEXT``, ``CONTENT_SHA256``) is handled by
    # snake tokens + KEEP so ``E2E`` is not misread as ``E2``.
    if name != name.upper() and name != name.lower():
        if _camel_task_tokens(name):
            return True
    return False


def _name_has_task_number(name: str) -> bool:
    """Production-symbol matcher (legacy, allowlist-backed)."""
    return bool(_TASK_NUMBER_NAME_RE.search("_" + name)) or bool(
        _TASK_CODE_IDENTIFIER_RE.search(name)
    )


# Existing stock of task-numbered test files (relative to services/api).
# RATCHET: only shrink this list. Renamed/deleted files must have their
# entry removed in the same change; new task-numbered file names are
# forbidden and must be renamed to business names instead.
TASK_NUMBER_TEST_FILE_ALLOWLIST: frozenset[str] = frozenset()

# Production-symbol allowlist is empty after TEST-GOVERNANCE production-symbol
# governance: D5/D6/ZPlus/R6 Python identifiers renamed to business names.
# Wire strings (failure_code values, ZPLUS_* policy/job_type constants, env
# values CLAREAD_R4_A3_*) remain durable contracts and are not scanned.
TASK_NUMBER_PRODUCTION_SYMBOL_ALLOWLIST: frozenset[str] = frozenset()

# P3 closeout: identifier allowlist is empty. Former entries (R4_A3_*_ENV
# Python names, schema-health d5/d6 test names, round0 test name) were
# not external contracts — only the env *string values* ``CLAREAD_R4_A3_*``
# and production D5/D6 symbols are durable, and those live outside this
# scan (strings exempt; production allowlist separate).
TASK_NUMBER_TEST_IDENTIFIER_ALLOWLIST: frozenset[str] = frozenset()


def _test_file_relpaths() -> list[str]:
    return sorted(
        path.relative_to(SERVICE_ROOT).as_posix()
        for path in TESTS_DIR.rglob("*.py")
        if path.name != "__init__.py"
    )


def test_new_test_file_names_carry_no_task_numbers() -> None:
    """Task-numbered test file names are legacy stock only (ratchet)."""
    actual = {
        rel
        for rel in _test_file_relpaths()
        if identifier_has_task_code(Path(rel).stem)
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
            f"{rel}:{name}" for name in names if identifier_has_task_code(name)
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
    """Positive samples: task-coded forms the detector must catch.

    These names are synthetic proof cases only — they must never be
    allowlisted. If a sample stops matching, the detector has regressed.
    """
    for name in (
        "test_a6_feature",
        "test_a01_feature",
        "test_l2_feature",
        "TestR16Feature",
        "TestT58aFeature",
        "TestP2cFeature",
        "TestR1aIntegration",
        "TestI4xSections",
        "d2_schema_pool",
        "test_round20_feature",
        "test_r6_stale_stream_reconcile",
        "test_t5_synthetic_single_digit_token",
        "i4x_env",
        "TestRound2SyntheticGates",
        "TestSyntheticI3ZSections",
    ):
        assert identifier_has_task_code(name), name


def test_task_code_pattern_passes_business_names() -> None:
    """Negative samples: business / technical identities must pass."""
    for name in (
        "test_budget_stop_report_counts",
        "test_spans_order_consistent_with_reading_order",
        "_make_write_chunk",
        "TestFreezePersistenceSqlOrder",
        "advance_round",
        "grammar_window_service",
        "layer1_fnv1a32",
        "test_utf8_encode_roundtrip",
        "test_utf8_roundtrip",
        "i18n_labels",
        "e2e_env",
        "test_v2_contract",
        "TestG1RepresentationEvents",
        "TestTecd3ProviderFallback",
        "_DEFAULT_CONTENT_SHA256",
        "_MINIMAL_SEED_BASE_TEXT_UTF16_LEN",
        "CONTENT_SHA256",
    ):
        assert not identifier_has_task_code(name), name
