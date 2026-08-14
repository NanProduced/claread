"""DATA-LEGACY-IDENTITY-EXIT zero-residual guard.

Locks L-GATE for DATA-LEGACY-IDENTITY-EXIT-LONG:

1. No production module under ``app/`` reads or writes the seven legacy
   analysis tables (exact names — never a wildcard).
2. The render-scene validation surface (``load_render_scene`` /
   ``validate_*_against_render_scene``) is gone from ``app/``.
3. The physically deleted legacy modules (``user_assets/records.py``,
   ``text_anchors.py``, ``schemas/user_assets/records.py``) have no
   importers and no surviving files anywhere in ``app/``.

DATA-SCHEMA-BASELINE has since dropped those legacy columns from the
baseline schema; the guard below still locks the code-level exit (the seven
legacy tables and the render-scene helpers must never reappear in ``app/``).

DATA- extends the guard to the 13 dropped identity columns:
``analysis_record_id`` (7 tables), ``anchor_sentence_id``,
``annotation_type`` (feedback), ``record_id`` (ai_usage_events /
dict_ai_candidate_entries) and ``task_id`` (ai_usage_events /
user_credit_ledger).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1] / "app"

LEGACY_ANALYSIS_TABLES = (
    "analysis_records",
    "analysis_results",
    "analysis_tasks",
    "analysis_task_events",
    "analysis_overview_tasks",
    "analysis_overview_task_events",
    "analysis_debug_snapshots",
)

RENDER_SCENE_SYMBOLS = (
    "load_render_scene",
    "validate_text_range_against_render_scene",
    "validate_multi_text_against_render_scene",
)

# Physically deleted in the commit; they must never reappear.
DELETED_LEGACY_MODULES = {
    APP_ROOT / "services" / "user_assets" / "records.py",
    APP_ROOT / "services" / "text_anchors.py",
    APP_ROOT / "schemas" / "user_assets" / "records.py",
}

# Dropped-column identifiers banned everywhere in ``app/``.
DROPPED_COLUMN_IDENTS = (
    "analysis_record_id",
    "anchor_sentence_id",
)

# Defense-in-depth redaction rules keep the dropped identity key names on
# purpose so leaked reasoning text is still scrubbed.
DROPPED_COLUMN_ALLOWLIST = {
    APP_ROOT / "services" / "reader_record_ask" / "reasoning_projection.py",
    APP_ROOT / "services" / "reader_record_ask" / "learner_reasoning" / "scrub.py",
}

# Write/read paths of ai_usage_events and user_credit_ledger where the
# dropped ``task_id`` / ``record_id`` columns must never reappear.
DROPPED_TASK_RECORD_FILES = (
    APP_ROOT / "services" / "ai_usage" / "service.py",
    APP_ROOT / "services" / "credits.py",
    APP_ROOT / "services" / "quota" / "ledger.py",
    APP_ROOT / "api" / "routes" / "dict.py",
    APP_ROOT / "api" / "routes" / "quota.py",
    APP_ROOT / "schemas" / "quota.py",
)

# The feedback surface must never resurrect the dropped ``annotation_type``
# column or the retired scopes.
FEEDBACK_SURFACE_FILES = (
    APP_ROOT / "services" / "feedback" / "service.py",
    APP_ROOT / "api" / "routes" / "feedback.py",
    APP_ROOT / "schemas" / "feedback.py",
)


def _python_files():
    for path in sorted(APP_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def test_no_legacy_analysis_table_access_in_app() -> None:
    offenders: list[str] = []
    for path in _python_files():
        text = path.read_text(encoding="utf-8")
        for table in LEGACY_ANALYSIS_TABLES:
            if re.search(rf"\b{re.escape(table)}\b", text):
                offenders.append(f"{path.relative_to(APP_ROOT)}: {table}")
    assert offenders == [], (
        "legacy analysis table access found:\n" + "\n".join(offenders)
    )


def test_no_render_scene_validation_surface_in_app() -> None:
    offenders: list[str] = []
    for path in _python_files():
        text = path.read_text(encoding="utf-8")
        for symbol in RENDER_SCENE_SYMBOLS:
            if re.search(rf"\b{re.escape(symbol)}\b", text):
                offenders.append(f"{path.relative_to(APP_ROOT)}: {symbol}")
    assert offenders == [], (
        "render-scene validation surface found:\n" + "\n".join(offenders)
    )


def test_deleted_legacy_modules_stay_deleted() -> None:
    resurrected = [str(p.relative_to(APP_ROOT)) for p in DELETED_LEGACY_MODULES if p.exists()]
    assert resurrected == [], (
        "deleted legacy modules reappeared:\n" + "\n".join(resurrected)
    )


def _imports_of(path: Path) -> set[str]:
    """Dotted module names imported by ``path``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
        elif isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
    return names


def test_no_importers_of_deleted_legacy_modules() -> None:
    deleted_modules = {
        "app.services.user_assets.records",
        "app.services.text_anchors",
        "app.schemas.user_assets.records",
    }
    importers: list[str] = []
    for path in _python_files():
        imported = _imports_of(path)
        for module in deleted_modules:
            if any(name == module or name.startswith(module + ".") for name in imported):
                importers.append(f"{path.relative_to(APP_ROOT)} -> {module}")
    assert importers == [], (
        "deleted legacy modules still have importers:\n" + "\n".join(importers)
    )


def test_no_dropped_identity_column_idents_in_app() -> None:
    offenders: list[str] = []
    for path in _python_files():
        if path in DROPPED_COLUMN_ALLOWLIST:
            continue
        text = path.read_text(encoding="utf-8")
        for ident in DROPPED_COLUMN_IDENTS:
            if re.search(rf"\b{ident}\b", text):
                offenders.append(f"{path.relative_to(APP_ROOT)}: {ident}")
    assert offenders == [], (
        "dropped identity column identifiers reappeared:\n" + "\n".join(offenders)
    )


def test_no_task_record_identity_in_usage_credit_paths() -> None:
    pattern = re.compile(r"(?<![A-Za-z0-9_])(task_id|record_id)\b")
    offenders: list[str] = []
    for path in DROPPED_TASK_RECORD_FILES:
        text = path.read_text(encoding="utf-8")
        offenders.extend(
            f"{path.relative_to(APP_ROOT)}: {match.group(0)}"
            for match in pattern.finditer(text)
        )
    assert offenders == [], (
        "dropped task_id/record_id columns reappeared in usage/credit paths:\n"
        + "\n".join(offenders)
    )


def test_no_legacy_identity_in_feedback_surface() -> None:
    pattern = re.compile(r"\bannotation_type\b")
    offenders: list[str] = []
    for path in FEEDBACK_SURFACE_FILES:
        text = path.read_text(encoding="utf-8")
        offenders.extend(
            f"{path.relative_to(APP_ROOT)}: {match.group(0)}"
            for match in pattern.finditer(text)
        )
    assert offenders == [], (
        "dropped feedback.annotation_type column reappeared:\n"
        + "\n".join(offenders)
    )
