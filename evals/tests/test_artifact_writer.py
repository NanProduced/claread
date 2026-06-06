import json
from pathlib import Path

import pytest

from claread_eval.schemas.dataset import EvalCase
from claread_eval.schemas.report import CaseSummary, EvalReport
from claread_eval.schemas.run import EvalCaseArtifact, EvalRunConfig
from claread_eval.writer.artifact_writer import (
    ArtifactWriteError,
    init_run_dir,
    write_case_artifact,
    write_case_index,
    write_report,
)
from claread_eval.writer.dataset_writer import (
    DatasetWriteError,
    expected_readiness_warnings,
    save_case_to_dataset,
)
from claread_eval.writer.sanitizer import ArtifactSanitizationError, sanitized_artifact_payload


@pytest.fixture
def run_config() -> EvalRunConfig:
    return EvalRunConfig(
        run_id="test-run-001",
        dataset_id="test-dataset",
    )


@pytest.fixture
def sample_artifact() -> EvalCaseArtifact:
    return EvalCaseArtifact(
        case_id="case-001",
        run_id="test-run-001",
        output={"schema_version": "3.0.0"},
        user_facing_state="normal",
    )


def test_init_run_dir(tmp_path: Path, run_config: EvalRunConfig) -> None:
    run_dir = init_run_dir(tmp_path, run_config)
    assert run_dir.is_dir()
    assert (run_dir / "run.json").is_file()
    assert (run_dir / "cases").is_dir()

    run_data = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert run_data["run_id"] == "test-run-001"


def test_init_run_dir_immutable(tmp_path: Path, run_config: EvalRunConfig) -> None:
    init_run_dir(tmp_path, run_config)
    with pytest.raises(ArtifactWriteError, match="already exists"):
        init_run_dir(tmp_path, run_config)


def test_write_case_artifact(
    tmp_path: Path, run_config: EvalRunConfig, sample_artifact: EvalCaseArtifact
) -> None:
    run_dir = init_run_dir(tmp_path, run_config)
    case_path = write_case_artifact(run_dir, sample_artifact)
    assert case_path.is_file()
    assert case_path.name == "case-001.json"

    data = json.loads(case_path.read_text(encoding="utf-8"))
    assert data["case_id"] == "case-001"


def test_write_case_artifact_immutable(
    tmp_path: Path, run_config: EvalRunConfig, sample_artifact: EvalCaseArtifact
) -> None:
    run_dir = init_run_dir(tmp_path, run_config)
    write_case_artifact(run_dir, sample_artifact)
    with pytest.raises(ArtifactWriteError, match="already exists"):
        write_case_artifact(run_dir, sample_artifact)


def test_write_case_index(
    tmp_path: Path, run_config: EvalRunConfig, sample_artifact: EvalCaseArtifact
) -> None:
    sample_artifact.grader_results = [
        {
            "grader_name": "schema_presence",
            "verdict": "fail",
            "severity": "hard",
            "metric": "schema",
            "evidence": "missing field",
        }
    ]
    sample_artifact.usage_summary.total_tokens = 12
    run_dir = init_run_dir(tmp_path, run_config)
    write_case_artifact(run_dir, sample_artifact)

    index_path = write_case_index(run_dir, run_config, [sample_artifact])

    data = json.loads(index_path.read_text(encoding="utf-8"))
    assert data["schema_version"] == "eval-case-index-v1"
    assert data["run_id"] == "test-run-001"
    assert data["total_cases"] == 1
    assert data["cases"][0]["case_id"] == "case-001"
    assert data["cases"][0]["artifact_href"] == "cases/case-001.json"
    assert data["cases"][0]["hard_failures"] == 1
    assert data["cases"][0]["total_tokens"] == 12

    with pytest.raises(ArtifactWriteError, match="Case index already exists"):
        write_case_index(run_dir, run_config, [sample_artifact])


def test_write_case_artifact_rejects_sensitive_fields(
    tmp_path: Path, run_config: EvalRunConfig
) -> None:
    run_dir = init_run_dir(tmp_path, run_config)
    artifact = EvalCaseArtifact(
        case_id="case-sensitive",
        run_id="test-run-001",
        model_identity={"model_settings": {"api_key": "secret"}},
    )

    with pytest.raises(ArtifactSanitizationError, match="api_key"):
        write_case_artifact(run_dir, artifact)

    assert not (run_dir / "cases" / "case-sensitive.json").exists()


def test_write_case_artifact_strip_mode_removes_sensitive_fields() -> None:
    artifact = EvalCaseArtifact(
        case_id="case-sensitive",
        run_id="test-run-001",
        model_identity={
            "model_settings": {
                "temperature": 0.2,
                "api_key": "secret",
                "password": "secret",
            }
        },
    )

    payload = sanitized_artifact_payload(artifact, mode="strip")

    model_settings = payload["model_identity"]["model_settings"]
    assert model_settings == {"temperature": 0.2}
    assert payload["artifact_sanitization"]["mode"] == "strip"
    assert "model_identity.model_settings.api_key" in payload["artifact_sanitization"][
        "removed_fields"
    ]


def test_sanitizer_detects_generic_token_fields() -> None:
    artifact = EvalCaseArtifact(
        case_id="case-sensitive-token",
        run_id="test-run-001",
        model_identity={"model_settings": {"token": "secret"}},
    )

    with pytest.raises(ArtifactSanitizationError, match="token"):
        sanitized_artifact_payload(artifact)


def test_sanitizer_redacts_secret_like_string_values_in_strip_mode() -> None:
    artifact = EvalCaseArtifact(
        case_id="case-secret-value",
        run_id="test-run-001",
        warnings=[
            {
                "code": "upstream_error",
                "level": "warning",
                "message": "upstream returned sk-testsecret123456",
            }
        ],
    )

    payload = sanitized_artifact_payload(artifact, mode="strip")

    assert payload["warnings"][0]["message"] == "<redacted>"
    assert "warnings[0].message" in payload["artifact_sanitization"]["removed_fields"]


def test_init_run_dir_rejects_sensitive_model_selection(tmp_path: Path) -> None:
    run_config = EvalRunConfig(
        run_id="sensitive-run-001",
        dataset_id="test-dataset",
        model_selection={"api_key": "secret"},
    )

    with pytest.raises(ArtifactSanitizationError, match="api_key"):
        init_run_dir(tmp_path, run_config)


def test_write_report(tmp_path: Path, run_config: EvalRunConfig) -> None:
    run_dir = init_run_dir(tmp_path, run_config)

    report = EvalReport(
        run_id="test-run-001",
        dataset_id="test-dataset",
        total_cases=1,
        passed=1,
        case_summaries=[
            CaseSummary(case_id="case-001", verdict="pass"),
        ],
    )

    json_path, md_path = write_report(run_dir, report)
    assert json_path.is_file()
    assert md_path.is_file()

    report_data = json.loads(json_path.read_text(encoding="utf-8"))
    assert report_data["run_id"] == "test-run-001"
    assert report_data["passed"] == 1

    md_content = md_path.read_text(encoding="utf-8")
    assert "# Eval Report: test-run-001" in md_content


def test_save_case_to_dataset(tmp_path: Path) -> None:
    dataset_dir = tmp_path / "dataset"
    dataset_dir.mkdir()
    (dataset_dir / "dataset.yaml").write_text("id: dataset\n", encoding="utf-8")
    case = EvalCase(
        id="case-001",
        text="Sentence one.",
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )

    path = save_case_to_dataset(dataset_dir, case)
    assert path.name == "case-001.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["id"] == "case-001"
    assert data["origin"] == "dataset"

    with pytest.raises(DatasetWriteError):
        save_case_to_dataset(dataset_dir, case)


def test_save_case_to_dataset_converts_ad_hoc_origin(tmp_path: Path) -> None:
    dataset_dir = tmp_path / "dataset"
    dataset_dir.mkdir()
    (dataset_dir / "dataset.yaml").write_text("id: dataset\n", encoding="utf-8")
    case = EvalCase(
        id="adhoc-001",
        origin="adhoc",
        text="Sentence one.",
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )

    path = save_case_to_dataset(dataset_dir, case)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["origin"] == "dataset"


def test_save_case_to_dataset_can_require_explicit_expected(tmp_path: Path) -> None:
    dataset_dir = tmp_path / "dataset"
    dataset_dir.mkdir()
    (dataset_dir / "dataset.yaml").write_text("id: dataset\n", encoding="utf-8")
    case = EvalCase(
        id="adhoc-001",
        origin="adhoc",
        text="Sentence one.",
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )

    warnings = expected_readiness_warnings(case)

    assert "min_translation_coverage is 0.0" in warnings
    with pytest.raises(DatasetWriteError, match="expected fields are not ready"):
        save_case_to_dataset(dataset_dir, case, require_explicit_expected=True)
