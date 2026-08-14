# task-history: TEST-GOVERNANCE-GATE-A-SAFE-REBUILD-R1 / G0
"""Evals ownership seam for the repository's single Python governance parser."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
API_GUARD_PATH = (
    REPO_ROOT / "services" / "api" / "tests" / "test_task_number_naming_guard.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "claread_task_number_naming_guard",
    API_GUARD_PATH,
)
assert _SPEC is not None and _SPEC.loader is not None
_API_GUARD = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _API_GUARD
_SPEC.loader.exec_module(_API_GUARD)


def _eval_items():
    return _API_GUARD.scan_python_and_config(path_prefix="evals/")


def test_evals_guard_reuses_the_authoritative_python_parser() -> None:
    assert _API_GUARD.REPO_ROOT == REPO_ROOT
    items = _eval_items()
    assert items
    assert any(item.kind == "python_comment" for item in items)
    assert any(item.kind == "python_docstring" for item in items)
    assert any(item.kind == "python_string_literal" for item in items)
    assert any(item.kind == "python_identifier" for item in items)


def test_evals_guard_mandatory_boundary_samples() -> None:
    assert _API_GUARD.task_tokens("Phase " + "2 in process prose")
    assert not _API_GUARD.task_tokens(
        "CEFR A1/A2/B1/B2/C1/C2 business fields"
    )
    wire = _API_GUARD.KEEP_WIRE_TOKENS[0]
    assert _API_GUARD.task_tokens(f"{wire}; " + "R" + "7 cleanup") == ("R7",)


def test_evals_changed_scope_has_no_mechanical_damage() -> None:
    assert not _API_GUARD.damage_hits(
        _API_GUARD.scan_python(
            Path(__file__),
            "evals/tests/test_task_number_naming_guard.py",
        )
    )

def test_evals_has_no_task_history_residuals() -> None:
    hits = _API_GUARD.residual_hits(_eval_items())
    assert not hits, (
        "Evals task-history residuals remain; expected RED until its accepted "
        f"rolling units complete (reported={min(len(hits), 80)}, total={len(hits)}): "
        f"{_API_GUARD._format_hits(hits)}"
    )
