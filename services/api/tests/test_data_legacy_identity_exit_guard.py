"""DATA-LEGACY-IDENTITY-EXIT zero-residual guard.

Locks L-GATE for DATA-LEGACY-IDENTITY-EXIT-LONG:

1. No production module under ``app/`` reads or writes the seven legacy
   analysis tables (exact names — never a wildcard).
2. The render-scene validation surface (``load_render_scene`` /
   ``validate_*_against_render_scene``) is gone from ``app/``.
3. The P1 deletion-pending files (``user_assets/records.py``,
   ``text_anchors.py``, ``schemas/user_assets/records.py``) have ZERO
   importers anywhere in ``app/`` — their physical deletion lands in the
   P1 commit, and this guard shrinks to a full scan at that point.

Deliberately NOT banned: ``analysis_record_id`` on protected shared tables
(reader_ask_*, user_annotations, reader_notes, favorite_records, feedback,
dict_ai_candidate_entries, ai_usage_events) — those columns stay until the
D2 schema drop, and new-chain fences / scrubbers legitimately reference
them. The ban is on the seven legacy TABLES and the render-scene helpers.
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

# Physical deletion lands in the P1 commit; until then they must have zero
# importers. Remove these entries together with the files in P1.
P1_DELETION_PENDING = {
    APP_ROOT / "services" / "user_assets" / "records.py",
    APP_ROOT / "services" / "text_anchors.py",
    APP_ROOT / "schemas" / "user_assets" / "records.py",
}


def _python_files():
    for path in sorted(APP_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def test_no_legacy_analysis_table_access_in_app() -> None:
    offenders: list[str] = []
    for path in _python_files():
        if path in P1_DELETION_PENDING:
            continue
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
        if path in P1_DELETION_PENDING:
            continue
        text = path.read_text(encoding="utf-8")
        for symbol in RENDER_SCENE_SYMBOLS:
            if re.search(rf"\b{re.escape(symbol)}\b", text):
                offenders.append(f"{path.relative_to(APP_ROOT)}: {symbol}")
    assert offenders == [], (
        "render-scene validation surface found:\n" + "\n".join(offenders)
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


def test_p1_pending_files_have_zero_importers() -> None:
    pending_modules = {
        "app.services.user_assets.records",
        "app.services.text_anchors",
        "app.schemas.user_assets.records",
    }
    importers: list[str] = []
    for path in _python_files():
        if path in P1_DELETION_PENDING:
            continue
        imported = _imports_of(path)
        for module in pending_modules:
            if any(name == module or name.startswith(module + ".") for name in imported):
                importers.append(f"{path.relative_to(APP_ROOT)} -> {module}")
    assert importers == [], (
        "P1 deletion-pending modules still have importers:\n" + "\n".join(importers)
    )
