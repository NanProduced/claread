"""P-3F: daily reader workflow harness artifact contract (offline).

Locks the abort-path usage closed loop: when the graph aborts before
``daily_projection_node`` (``usage_summary`` is None), the eval artifact
must fall back to the workflow's existing ``_aggregate_usage`` instead of
recording a usage-less abort, and must keep the existing
``refinement_result`` failure evidence so paragraph-notes and refinement
failures stay distinguishable.

Fully offline: ``app.services.daily_reader.workflow`` is stubbed in
``sys.modules`` — no provider, DB, or services/api imports run here. The
real ``_aggregate_usage`` math is locked by services/api tests; these
tests lock the harness wiring (fallback expression + artifact fields).
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

_HARNESS_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "daily_reader_workflow_harness.py"
)


def _load_harness_module():
    spec = importlib.util.spec_from_file_location(
        "daily_reader_workflow_harness_under_test", _HARNESS_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def harness():
    return _load_harness_module()


def _install_workflow_stub(monkeypatch: pytest.MonkeyPatch, aggregate_usage) -> None:
    """Stub the harness's lazy ``_aggregate_usage`` import target."""
    stub = types.ModuleType("app.services.daily_reader.workflow")
    stub._aggregate_usage = aggregate_usage
    for name in ("app", "app.services", "app.services.daily_reader"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "app.services.daily_reader.workflow", stub)


def _abort_final_state() -> dict:
    """Synthetic aborted run: projection never executed, nodes hold usage."""
    return {
        "abort": True,
        "usage_summary": None,
        "body_json": {},
        "highlights_json": [],
        "paragraph_notes_json": {"notes": []},
        "takeaways_json": {"title_zh": "失败阶段可归因"},
        "review_result": {
            "passed": False,
            "reason": "quality_review_rejected",
            "issues": [
                {
                    "dimension": "paragraph_note_coverage",
                    "severity": "major",
                    "description": "有实质内容的 reading unit 缺少完整透读 note: p_0",
                    "suggestion": "仅为这些 reading unit 补充导读、摘要和段落译文",
                }
            ],
        },
        "refinement_result": {
            "abort": True,
            "remaining_issues": [
                {
                    "dimension": "refinement_failed",
                    "severity": "major",
                    "description": "定向修正执行失败",
                    "suggestion": "保留草稿并由后续重试或人工审核处理",
                }
            ],
        },
        "paragraph_notes_usage": {
            "input_tokens": 44,
            "output_tokens": 28,
            "total_tokens": 72,
            "model_requests": 4,
            "tool_calls": 0,
        },
        "refinement_usage": {
            "input_tokens": 20,
            "output_tokens": 10,
            "total_tokens": 30,
            "model_requests": 4,
            "tool_calls": 0,
        },
    }


def _case() -> dict:
    return {
        "case_id": "syn-abort-usage-001",
        "input": {
            "title": "Abort usage closed loop",
            "original_text": "Substantive article body.",
            "source": "synthetic",
        },
    }


def test_abort_artifact_uses_aggregate_usage_fallback(harness, monkeypatch) -> None:
    final_state = _abort_final_state()
    seen_states: list[dict] = []

    def fake_aggregate_usage(state):
        seen_states.append(state)
        return {
            "available": True,
            "per_agent": {
                "paragraph_notes": final_state["paragraph_notes_usage"],
                "refinement": final_state["refinement_usage"],
            },
            "aggregate": {
                "input_tokens": 64,
                "output_tokens": 38,
                "total_tokens": 102,
                "model_requests": 8,
                "tool_calls": 0,
            },
        }

    _install_workflow_stub(monkeypatch, fake_aggregate_usage)

    artifact = harness.build_artifact(_case(), final_state)

    # fallback fired exactly once, on the real final_state
    assert seen_states == [final_state]

    # conserved usage: aggregate is the per-agent field-wise sum
    assert artifact["usage_summary"]["available"] is True
    assert artifact["usage_summary"]["per_agent"] == {
        "paragraph_notes": final_state["paragraph_notes_usage"],
        "refinement": final_state["refinement_usage"],
    }
    assert artifact["usage_summary"]["aggregate"] == {
        "input_tokens": 44 + 20,
        "output_tokens": 28 + 10,
        "total_tokens": 72 + 30,
        "model_requests": 4 + 4,
        "tool_calls": 0 + 0,
    }

    # failure evidence survives so the abort stage stays attributable
    assert artifact["abort"] is True
    assert artifact["refinement_result"] == final_state["refinement_result"]
    assert artifact["review_result"] == final_state["review_result"]


def test_artifact_prefers_existing_usage_summary(harness, monkeypatch) -> None:
    final_state = _abort_final_state()
    final_state["abort"] = False
    final_state["usage_summary"] = {
        "available": True,
        "per_agent": {"vocab": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}},
        "aggregate": {
            "input_tokens": 1,
            "output_tokens": 1,
            "total_tokens": 2,
            "model_requests": 1,
            "tool_calls": 0,
        },
    }

    def unexpected_aggregate_usage(state):  # pragma: no cover - must not run
        raise AssertionError("_aggregate_usage must not run when usage_summary is set")

    _install_workflow_stub(monkeypatch, unexpected_aggregate_usage)

    artifact = harness.build_artifact(_case(), final_state)

    assert artifact["usage_summary"] == final_state["usage_summary"]
    assert artifact["abort"] is False
