from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "aggregate_translation_grouping_eval.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "translation_grouping_aggregate",
    _SCRIPT_PATH,
)
assert _SPEC is not None and _SPEC.loader is not None
aggregate_eval = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(aggregate_eval)


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_completed_translation_grouping_eval_is_reproducible(
    monkeypatch,
    tmp_path: Path,
) -> None:
    manifest = _read_json(aggregate_eval.DATASET_DIR / "manifest.json")
    manifest_ids = {entry["id"] for entry in manifest}
    judgment_ids = {path.stem for path in aggregate_eval.JUDGMENTS_DIR.glob("*.json")}
    side_assignment = _read_json(aggregate_eval.SIDE_ASSIGNMENT_PATH)
    assignment_ids = set(side_assignment["articles"])

    assert len(manifest_ids) == 24
    assert judgment_ids == manifest_ids
    assert assignment_ids == manifest_ids
    assert all(
        set(assignment.values()) == {"deterministic", "semantic"} and set(assignment) == {"X", "Y"}
        for assignment in side_assignment["articles"].values()
    )

    recomputed_json = tmp_path / "aggregate.json"
    recomputed_report = tmp_path / "report.md"
    monkeypatch.setattr(aggregate_eval, "REPORT_JSON", recomputed_json)
    monkeypatch.setattr(aggregate_eval, "REPORT_MD", recomputed_report)
    aggregate_eval.main()

    committed = _read_json(aggregate_eval.DATASET_DIR / "results" / "aggregate.json")
    assert _read_json(recomputed_json) == committed
    assert committed["problems"] == []
    assert committed["limitations"]
    assert committed["interpretation"]["production_planner_authorized"] is False
    assert "does not authorize" in recomputed_report.read_text(encoding="utf-8")
