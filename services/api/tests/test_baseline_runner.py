"""Tests for baseline runner CLI argument parsing and manifest generation."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import patch

# Add project root to path so the script can be imported
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.run_p41b_baseline import build_parser, run_single_node_probe  # noqa: E402


def test_baseline_parser_accepts_repair_mode() -> None:
    """--repair-mode patch is parsed correctly."""
    parser = build_parser()
    args = parser.parse_args(["--sample", "sample-1", "--repair-mode", "patch"])
    assert args.repair_mode == "patch"


def test_baseline_parser_accepts_full_result_repair_mode() -> None:
    """--repair-mode full_result is parsed correctly."""
    parser = build_parser()
    args = parser.parse_args(["--sample", "sample-1", "--repair-mode", "full_result"])
    assert args.repair_mode == "full_result"


def test_baseline_parser_defaults_repair_mode() -> None:
    """Default repair_mode is patch."""
    parser = build_parser()
    args = parser.parse_args(["--sample", "sample-1"])
    assert args.repair_mode == "patch"


def test_baseline_parser_rejects_invalid_repair_mode() -> None:
    """Invalid repair_mode is rejected."""
    parser = build_parser()
    try:
        parser.parse_args(["--sample", "sample-1", "--repair-mode", "invalid"])
        raise AssertionError("Should have raised SystemExit")
    except SystemExit:
        pass


def test_baseline_dry_run_no_real_llm() -> None:
    """Dry-run (no --run-real) does not call real LLM."""
    from scripts.run_p41b_baseline import ensure_real_run_allowed  # noqa: E402

    # No exception for dry-run
    ensure_real_run_allowed(run_real=False, all_samples=False)

    # Real run without env should fail
    original = os.environ.get("CLAREAD_ALLOW_REAL_LLM_TESTS")
    os.environ.pop("CLAREAD_ALLOW_REAL_LLM_TESTS", None)
    try:
        try:
            ensure_real_run_allowed(run_real=True, all_samples=False)
            raise AssertionError("Should have raised SystemExit")
        except SystemExit:
            pass
    finally:
        if original is not None:
            os.environ["CLAREAD_ALLOW_REAL_LLM_TESTS"] = original


def test_node_probe_metrics_include_repair_mode() -> None:
    """run_single_node_probe metrics dict must contain repair_mode."""
    from app.eval_adapter.schemas import (
        ArticleAnalysisNodeProbeResult,
        PromptIdentity,
        RequestSnapshot,
        SchemaIdentity,
        WorkflowIdentity,
    )

    fake_result = ArticleAnalysisNodeProbeResult(
        status="succeeded",
        request_snapshot=RequestSnapshot(
            request_id="test",
            source_text_hash="h",
            source_char_count=6,
            reading_goal="daily_reading",
            reading_variant="intermediate_reading",
            source_type="user_input",
            extended=False,
            rag_mode="off",
            trace_scope="off",
        ),
        workflow_identity=WorkflowIdentity(
            workflow_name="article_analysis",
            workflow_version="3.0.0",
            topology_mode="learning",
        ),
        schema_identity=SchemaIdentity(
            schema_version="3.0.0",
            render_schema_version="3.0.0",
            topology_mode="learning",
        ),
        prompt_identity=PromptIdentity(prompt_version="test"),
        node_name="vocabulary",
    )

    async def _fake_probe(_request):
        return fake_result

    sample = {"id": "test-sample", "text": "Hello.", "chars": 6}

    with (
        patch(
            "app.eval_adapter.node_probe.run_article_analysis_node_probe",
            _fake_probe,
        ),
        patch(
            "app.eval_adapter.shared.build_llm_config_snapshot",
            return_value=None,
        ),
    ):
        metrics = asyncio.run(
            run_single_node_probe(
                sample,
                node_name="vocabulary",
                model_profile="eval-profile",
                reading_goal="daily_reading",
                reading_variant="intermediate_reading",
                rag_mode="off",
                timeout_seconds=None,
                run_real=False,
                repair_mode="patch",
            )
        )

    assert metrics["repair_mode"] == "patch"
    assert metrics["target"] == "node-probe"
