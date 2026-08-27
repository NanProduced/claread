"""P-5D-R8.5: abort evidence persistence + draft-with-verdict (offline)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from daily_reader_teaching_v2_fixtures import (
    READING_UNITS,
    make_blueprint,
    make_language_support,
    make_review_pass,
    make_translation,
)

from app.services.daily_reader.discovery import DiscoveredArticle
from app.services.daily_reader.pipeline import (
    PipelineResult,
    _run_workflow_and_store,
    build_abort_error_evidence,
    collect_pipeline_alert_reasons,
    stores_quality_abort_as_draft,
)
from app.services.daily_reader.scoring import ArticleScore
from app.services.daily_reader.workflow import daily_projection_node


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _package_state() -> dict:
    blueprint = make_blueprint().model_dump()
    language_support = make_language_support().model_dump()
    package = {
        "comprehension_checkpoints": blueprint["comprehension_checkpoints"],
        "high_difficulty_unit_ids": language_support["high_difficulty_unit_ids"],
        "language_targets": language_support["language_targets"],
        "sentence_maps": language_support["sentence_maps"],
        "transfer_task": blueprint["transfer_task"],
        "translations_by_paragraph_id": {
            item["paragraph_id"]: item["translation"]
            for item in make_translation().model_dump()["translations"]
        },
    }
    return {
        "original_text": "\n\n".join(unit["text"] for unit in READING_UNITS),
        "reading_units": READING_UNITS,
        "lesson_blueprint": blueprint,
        "language_support": language_support,
        "learning_package": package,
        "derived_translation_unit_ids": ["u01", "u02", "u03"],
        "teaching_contract_issues": [],
        "semantic_review_result": make_review_pass().model_dump(),
        "source_url": "https://example.test/policy-analysis",
    }


def _review_fail_state() -> dict:
    return {
        "abort": True,
        "abort_reason": "frozen_derivation_field",
        "abort_diagnostics": {
            "field": "learning_package.language_targets[2].paragraph_id",
        },
        "semantic_review_result": {
            "verdict": "FAIL",
            "contract_results": [
                {"contract": "evidence_anchors", "passed": False, "note": "anchor miss"},
                {"contract": "difficulty_fit", "passed": True, "note": "ok"},
            ],
            "issues": [
                {
                    "contract": "evidence_anchors",
                    "field": "learning_package.language_targets[2].paragraph_id",
                    "problem": "retargets a frozen derivation input",
                    "rationale": "paragraph_id is a derive input",
                }
            ],
        },
        "refinement_result": {
            "rejection": {
                "violations": [
                    {
                        "container": "learning_package",
                        "error_type": "frozen_derivation_field",
                        "loc": ["language_targets", "paragraph_id"],
                    }
                ]
            }
        },
        "usage_summary": {
            "available": True,
            "aggregate": {
                "input_tokens": 12,
                "output_tokens": 4,
                "total_tokens": 16,
                "model_requests": 3,
                "tool_calls": 0,
            },
        },
    }


def test_abort_error_evidence_keeps_review_and_rejection() -> None:
    evidence = build_abort_error_evidence(_review_fail_state())
    assert evidence["verdict"] == "FAIL"
    assert evidence["failed_contracts"] == ["evidence_anchors"]
    assert evidence["issues"] == [
        {
            "contract": "evidence_anchors",
            "field": "learning_package.language_targets[2].paragraph_id",
            "problem": "retargets a frozen derivation input",
            "rationale": "paragraph_id is a derive input",
        }
    ]
    assert evidence["rejection_violations"][0]["error_type"] == "frozen_derivation_field"
    assert evidence["usage"]["model_requests"] == 3
    assert evidence["abort_diagnostics"]["field"].endswith("paragraph_id")


def test_abort_error_evidence_truncates_long_rationale() -> None:
    state = _review_fail_state()
    state["semantic_review_result"]["issues"][0]["rationale"] = "x" * 5000
    evidence = build_abort_error_evidence(state)
    assert len(evidence["issues"][0]["rationale"]) <= 400


def test_quality_abort_reasons_store_as_draft() -> None:
    assert stores_quality_abort_as_draft("teaching_v2_hard_gates_failed")
    assert stores_quality_abort_as_draft("teaching_v2_after_review_fail")
    assert stores_quality_abort_as_draft("frozen_derivation_field")
    assert not stores_quality_abort_as_draft("teaching_v2_artifact_schema_violation")
    assert not stores_quality_abort_as_draft("semantic_review_evidence_invalid")
    assert not stores_quality_abort_as_draft("refinement_stage_failed")
    assert not stores_quality_abort_as_draft("semantic_review_stage_failed")


def test_zero_output_alert_ignores_stored_drafts() -> None:
    empty = collect_pipeline_alert_reasons(PipelineResult())
    assert "zero_output" in empty
    drafts = collect_pipeline_alert_reasons(
        PipelineResult(articles=[{"id": "daily_x", "status": "draft"}])
    )
    assert "zero_output" not in drafts


def test_hard_gate_failure_still_emits_lesson_v2_for_draft() -> None:
    state = _package_state()
    state["learning_package"]["language_targets"][0]["expression"] = "substantive analyses"
    result = daily_projection_node(state)
    assert result["abort"] is True
    assert result["abort_reason"] == "teaching_v2_hard_gates_failed"
    assert result["lesson_v2"]["run_meta"]["outcome"] == "draft_with_verdict"
    assert "anchors_resolve" in result["lesson_v2"]["run_meta"]["quality"]["failed_gates"]


@pytest.mark.anyio
async def test_frozen_abort_stores_draft_and_records_evidence() -> None:
    article = DiscoveredArticle(
        url="https://example.test/frozen",
        title="Frozen candidate",
        source="source",
        description="Description",
        text="Substantive text " * 50,
        tags=["section"],
        word_count=800,
        needs_extraction=False,
    )
    state = _review_fail_state()
    state["lesson_blueprint"] = {
        "title_zh": "冻结尾",
        "effective_difficulty": "B1",
        "tags_zh": ["t"],
    }
    state["learning_package"] = {}
    state["reading_units"] = [{"id": "u01", "text": "Hello."}]
    graph = SimpleNamespace(ainvoke=AsyncMock(return_value=state))
    tracker = SimpleNamespace(add_error=AsyncMock(), update_stage=AsyncMock())
    stored: list[dict] = []

    async def fake_store(payload: dict) -> None:
        stored.append(payload)

    with (
        patch(
            "app.services.daily_reader.workflow.build_daily_reader_graph",
            return_value=graph,
        ),
        patch(
            "app.services.daily_reader.pipeline._record_daily_pipeline_event",
            new=AsyncMock(),
        ),
        patch(
            "app.services.daily_reader.pipeline._store_daily_reader",
            new=fake_store,
        ),
        patch(
            "app.services.daily_reader.pipeline._next_sequence_number",
            new=AsyncMock(return_value=1),
        ),
        patch(
            "app.services.daily_reader.pipeline.process_article_covers",
            new=AsyncMock(return_value=SimpleNamespace(cover_url=None, image_blocks=[], meta={})),
        ),
        patch(
            "app.services.daily_reader.pipeline.business_today",
            return_value=__import__("datetime").date(2026, 8, 26),
        ),
    ):
        result = await _run_workflow_and_store(
            article,
            ArticleScore(score=8.0, difficulty="B2", tags=["topic"]),
            tracker=tracker,
        )

    assert result is not None
    assert result["status"] == "draft"
    assert result["lesson_v2"]["run_meta"]["outcome"] == "draft_with_verdict"
    assert stored and stored[0]["status"] == "draft"
    tracker.add_error.assert_awaited()
    err_kwargs = tracker.add_error.await_args
    assert err_kwargs.args[0] == "workflow_abort"
    evidence = err_kwargs.kwargs.get("evidence") or (
        err_kwargs.args[2] if len(err_kwargs.args) > 2 else None
    )
    assert evidence["verdict"] == "FAIL"
    assert evidence["issues"][0]["field"].endswith("paragraph_id")


@pytest.mark.anyio
async def test_schema_violation_stays_hard_abort() -> None:
    article = DiscoveredArticle(
        url="https://example.test/schema",
        title="Schema boom",
        source="source",
        description="Description",
        text="Substantive text " * 50,
        tags=["section"],
        word_count=800,
        needs_extraction=False,
    )
    graph = SimpleNamespace(
        ainvoke=AsyncMock(
            return_value={
                "abort": True,
                "abort_reason": "teaching_v2_artifact_schema_violation",
                "abort_diagnostics": {"schema_errors": ["missing"]},
            }
        )
    )
    with (
        patch(
            "app.services.daily_reader.workflow.build_daily_reader_graph",
            return_value=graph,
        ),
        patch(
            "app.services.daily_reader.pipeline._record_daily_pipeline_event",
            new=AsyncMock(),
        ),
        patch(
            "app.services.daily_reader.pipeline._store_daily_reader",
            new=AsyncMock(),
        ) as store,
    ):
        result = await _run_workflow_and_store(
            article,
            ArticleScore(score=8.0, difficulty="B2", tags=["topic"]),
        )
    assert result is None
    store.assert_not_awaited()
