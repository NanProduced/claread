from pathlib import Path

import pytest

from claread_eval.adapter.fake_client import FakeArticleAnalysisAdapterClient
from claread_eval.graders.schema_presence import SchemaPresenceGrader
from claread_eval.graders.status_error import StatusErrorGrader
from claread_eval.graders.translation_coverage import TranslationCoverageGrader
from claread_eval.graders.warning_drop_summary import WarningDropSummaryGrader
from claread_eval.loader.dataset_loader import load_dataset
from claread_eval.runner.simple_runner import run_eval
from claread_eval.schemas.dataset import EvalCase
from claread_eval.schemas.grader import GraderSeverity, GraderVerdict
from claread_eval.schemas.run import EvalCaseArtifact, EvalRunConfig


def test_schema_presence_grader_pass() -> None:
    case = EvalCase(
        id="test-001",
        text="Hello.",
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )
    artifact = EvalCaseArtifact(
        case_id="test-001",
        run_id="r1",
        output={
            "schema_version": "3.0.0",
            "request": {},
            "article": {},
            "user_facing_state": "normal",
        },
    )
    result = SchemaPresenceGrader().grade(case, artifact)
    assert result.verdict == GraderVerdict.PASS


def test_schema_presence_grader_fail_missing_fields() -> None:
    case = EvalCase(
        id="test-002",
        text="Hello.",
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )
    artifact = EvalCaseArtifact(
        case_id="test-002",
        run_id="r1",
        output={"schema_version": "3.0.0"},
    )
    result = SchemaPresenceGrader().grade(case, artifact)
    assert result.verdict == GraderVerdict.FAIL
    assert result.severity == GraderSeverity.HARD


def test_schema_presence_grader_fail_error() -> None:
    case = EvalCase(
        id="test-003",
        text="Hello.",
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )
    artifact = EvalCaseArtifact(
        case_id="test-003",
        run_id="r1",
        error={"code": "RuntimeError", "message": "Something went wrong"},
    )
    result = SchemaPresenceGrader().grade(case, artifact)
    assert result.verdict == GraderVerdict.FAIL


def test_status_error_grader_normal() -> None:
    case = EvalCase(
        id="test-004",
        text="Hello.",
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )
    artifact = EvalCaseArtifact(
        case_id="test-004",
        run_id="r1",
        user_facing_state="normal",
    )
    result = StatusErrorGrader().grade(case, artifact)
    assert result.verdict == GraderVerdict.PASS


def test_status_error_grader_degraded_heavy() -> None:
    case = EvalCase(
        id="test-005",
        text="Hello.",
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )
    artifact = EvalCaseArtifact(
        case_id="test-005",
        run_id="r1",
        user_facing_state="degraded_heavy",
    )
    result = StatusErrorGrader().grade(case, artifact)
    assert result.verdict == GraderVerdict.FAIL
    assert result.severity == GraderSeverity.HARD


def test_status_error_grader_degraded_light() -> None:
    case = EvalCase(
        id="test-006",
        text="Hello.",
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )
    artifact = EvalCaseArtifact(
        case_id="test-006",
        run_id="r1",
        user_facing_state="degraded_light",
    )
    result = StatusErrorGrader().grade(case, artifact)
    assert result.verdict == GraderVerdict.FAIL
    assert result.severity == GraderSeverity.SOFT


def test_translation_coverage_grader_pass() -> None:
    case = EvalCase(
        id="test-007",
        text="Hello. World.",
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        expected={"min_translation_coverage": 1.0},
    )
    artifact = EvalCaseArtifact(
        case_id="test-007",
        run_id="r1",
        output={"article": {"sentences": [{"id": "s-0"}, {"id": "s-1"}]}},
        translations=[
            {"sentence_id": "s-0", "translation_zh": "你好"},
            {"sentence_id": "s-1", "translation_zh": "世界"},
        ],
    )
    result = TranslationCoverageGrader().grade(case, artifact)
    assert result.verdict == GraderVerdict.PASS


def test_translation_coverage_observes_when_threshold_not_configured() -> None:
    case = EvalCase(
        id="test-007b",
        text="Hello. World.",
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )
    artifact = EvalCaseArtifact(
        case_id="test-007b",
        run_id="r1",
        output={"article": {"sentences": [{"id": "s-0"}, {"id": "s-1"}]}},
        translations=[],
    )
    result = TranslationCoverageGrader().grade(case, artifact)
    assert result.verdict == GraderVerdict.PASS
    assert result.severity == GraderSeverity.INFO


def test_translation_coverage_grader_fail() -> None:
    case = EvalCase(
        id="test-008",
        text="Hello. World. Foo.",
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        expected={"min_translation_coverage": 1.0},
    )
    artifact = EvalCaseArtifact(
        case_id="test-008",
        run_id="r1",
        output={"article": {"sentences": [{"id": "s-0"}, {"id": "s-1"}, {"id": "s-2"}]}},
        translations=[
            {"sentence_id": "s-0", "translation_zh": "你好"},
        ],
    )
    result = TranslationCoverageGrader().grade(case, artifact)
    assert result.verdict == GraderVerdict.FAIL


def test_warning_drop_summary_grader_pass() -> None:
    case = EvalCase(
        id="test-009",
        text="Hello.",
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        expected={"allowed_warning_codes": [], "max_warning_count": 0},
    )
    artifact = EvalCaseArtifact(
        case_id="test-009",
        run_id="r1",
    )
    result = WarningDropSummaryGrader().grade(case, artifact)
    assert result.verdict == GraderVerdict.PASS


def test_warning_drop_summary_tolerates_default_info_warning() -> None:
    case = EvalCase(
        id="test-010",
        text="Hello.",
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        expected={"allowed_warning_codes": [], "max_warning_count": 1},
    )
    artifact = EvalCaseArtifact(
        case_id="test-010",
        run_id="r1",
        warnings=[
            {
                "code": "TEXT_TYPE_NEEDS_CARE",
                "level": "info",
                "message": "Needs care",
            }
        ],
    )
    result = WarningDropSummaryGrader().grade(case, artifact)
    assert result.verdict == GraderVerdict.PASS


def test_warning_drop_summary_observes_when_expected_not_configured() -> None:
    case = EvalCase(
        id="test-010b",
        text="Hello.",
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )
    artifact = EvalCaseArtifact(
        case_id="test-010b",
        run_id="r1",
        warnings=[
            {
                "code": "UNEXPECTED_WARNING",
                "level": "warning",
                "message": "Observed only",
            }
        ],
    )
    result = WarningDropSummaryGrader().grade(case, artifact)
    assert result.verdict == GraderVerdict.PASS
    assert result.severity == GraderSeverity.INFO


def test_status_error_grader_timeout_artifact() -> None:
    case = EvalCase(
        id="test-011",
        text="Hello.",
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )
    artifact = EvalCaseArtifact(
        case_id="test-011",
        run_id="r1",
        adapter_status="timeout",
        timeout=True,
        error={"code": "TimeoutError", "message": "Timed out"},
    )
    result = StatusErrorGrader().grade(case, artifact)
    assert result.verdict == GraderVerdict.FAIL
    assert result.severity == GraderSeverity.HARD


@pytest.mark.asyncio
async def test_report_generation_end_to_end(tmp_path: Path) -> None:
    dataset_path = Path(__file__).parent.parent / "datasets" / "article-analysis-v1"
    if not dataset_path.is_dir():
        pytest.skip("Real dataset not available")

    dataset, cases = load_dataset(dataset_path)
    adapter = FakeArticleAnalysisAdapterClient(latency_seconds=0.0)
    run_config = EvalRunConfig(
        run_id="report-test-001",
        dataset_id=dataset.id,
    )

    report = await run_eval(
        dataset=dataset,
        cases=cases,
        run_config=run_config,
        adapter=adapter,
        runs_root=str(tmp_path),
    )

    assert report.run_id == "report-test-001"
    assert report.dataset_id == "article-analysis-v1"
    assert report.total_cases == len(cases)
    assert report.passed > 0

    run_dir = tmp_path / "report-test-001"
    report_json = run_dir / "report.json"
    report_md = run_dir / "report.md"
    case_index = run_dir / "case-index.json"
    assert report_json.is_file()
    assert report_md.is_file()
    assert case_index.is_file()

    import json
    data = json.loads(report_json.read_text(encoding="utf-8"))
    assert data["total_cases"] == len(cases)
    index_data = json.loads(case_index.read_text(encoding="utf-8"))
    assert index_data["schema_version"] == "eval-case-index-v1"
    assert index_data["total_cases"] == len(cases)

    md = report_md.read_text(encoding="utf-8")
    assert "Eval Report" in md
