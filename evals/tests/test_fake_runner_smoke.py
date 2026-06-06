from pathlib import Path

import pytest

from claread_eval.adapter.fake_client import FakeArticleAnalysisAdapterClient
from claread_eval.loader.dataset_loader import load_dataset
from claread_eval.runner.simple_runner import run_eval
from claread_eval.schemas.run import EvalRunConfig


@pytest.fixture
def dataset_path() -> Path:
    return Path(__file__).parent.parent / "datasets" / "article-analysis-v1"


@pytest.mark.asyncio
async def test_fake_runner_smoke(dataset_path: Path, tmp_path: Path) -> None:
    if not dataset_path.is_dir():
        pytest.skip("Real dataset not available")

    dataset, cases = load_dataset(dataset_path)
    assert len(cases) >= 3

    adapter = FakeArticleAnalysisAdapterClient(latency_seconds=0.0)
    run_config = EvalRunConfig(
        run_id="smoke-test-001",
        dataset_id=dataset.id,
    )

    report = await run_eval(
        dataset=dataset,
        cases=cases,
        run_config=run_config,
        adapter=adapter,
        runs_root=str(tmp_path),
    )

    assert report.total_cases == len(cases)
    assert report.passed + report.failed + report.errored == report.total_cases
    assert len(adapter.calls) == len(cases)

    run_dir = tmp_path / "smoke-test-001"
    assert run_dir.is_dir()
    assert (run_dir / "run.json").is_file()
    assert (run_dir / "case-index.json").is_file()
    assert (run_dir / "report.json").is_file()
    assert (run_dir / "report.md").is_file()

    for case in cases:
        case_file = run_dir / "cases" / f"{case.id}.json"
        assert case_file.is_file(), f"Missing artifact for case {case.id}"


@pytest.mark.asyncio
async def test_fake_runner_single_case(tmp_path: Path) -> None:
    from claread_eval.schemas.dataset import EvalCase, EvalDataset

    dataset = EvalDataset(id="single-test", target="article_analysis")
    case = EvalCase(
        id="single-001",
        text="Hello world. This is a test sentence.",
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )

    adapter = FakeArticleAnalysisAdapterClient(latency_seconds=0.0)
    run_config = EvalRunConfig(
        run_id="single-run-001",
        dataset_id=dataset.id,
    )

    report = await run_eval(
        dataset=dataset,
        cases=[case],
        run_config=run_config,
        adapter=adapter,
        runs_root=str(tmp_path),
    )

    assert report.total_cases == 1
    assert report.passed == 1


@pytest.mark.asyncio
async def test_runner_persists_identity_fields(tmp_path: Path) -> None:
    from claread_eval.schemas.dataset import EvalCase, EvalDataset

    class IdentityAdapter:
        async def analyze(self, case, run_config):
            return {
                "status": "succeeded",
                "workflow_identity": {
                    "workflow_name": "article_analysis",
                    "workflow_version": "3.0.0",
                    "topology_mode": "learning",
                },
                "schema_identity": {
                    "schema_version": "3.0.0",
                    "render_schema_version": "3.0.0",
                    "topology_mode": "learning",
                },
                "prompt_identity": {
                    "prompt_version": "prompt-a",
                },
                "render_scene": {
                    "schema_version": "3.0.0",
                    "request": {},
                    "article": {},
                    "user_facing_state": "normal",
                    "translations": [],
                    "inline_marks": [],
                    "sentence_entries": [],
                    "warnings": [],
                },
            }

    dataset = EvalDataset(id="identity-test", target="article_analysis")
    case = EvalCase(
        id="identity-001",
        text="Hello world.",
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )
    run_config = EvalRunConfig(
        run_id="identity-run-001",
        dataset_id=dataset.id,
        prompt_variant_id="variant-a",
    )

    await run_eval(
        dataset=dataset,
        cases=[case],
        run_config=run_config,
        adapter=IdentityAdapter(),
        runs_root=str(tmp_path),
    )

    import json

    artifact_path = tmp_path / "identity-run-001" / "cases" / "identity-001.json"
    data = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert data["workflow_identity"]["workflow_name"] == "article_analysis"
    assert data["schema_identity"]["render_schema_version"] == "3.0.0"
    assert data["prompt_identity"]["prompt_version"] == "prompt-a"
    assert data["prompt_identity"]["prompt_variant_id"] == "variant-a"


@pytest.mark.asyncio
async def test_fake_adapter_persists_identity_fields(tmp_path: Path) -> None:
    from claread_eval.schemas.dataset import EvalCase, EvalDataset

    dataset = EvalDataset(id="fake-identity-test", target="article_analysis")
    case = EvalCase(
        id="fake-identity-001",
        text="Hello world.",
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )
    run_config = EvalRunConfig(
        run_id="fake-identity-run-001",
        dataset_id=dataset.id,
        prompt_version="prompt-fake",
        prompt_variant_id="variant-fake",
    )

    await run_eval(
        dataset=dataset,
        cases=[case],
        run_config=run_config,
        adapter=FakeArticleAnalysisAdapterClient(latency_seconds=0.0),
        runs_root=str(tmp_path),
    )

    import json

    artifact_path = tmp_path / "fake-identity-run-001" / "cases" / "fake-identity-001.json"
    data = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert data["workflow_identity"]["workflow_name"] == "article_analysis"
    assert data["schema_identity"]["render_schema_version"] == "3.0.0"
    assert data["prompt_identity"]["prompt_version"] == "prompt-fake"
    assert data["prompt_identity"]["prompt_variant_id"] == "variant-fake"


@pytest.mark.asyncio
async def test_fake_adapter_persists_prompt_snapshot_hash(tmp_path: Path) -> None:
    from claread_eval.schemas.dataset import EvalCase, EvalDataset

    dataset = EvalDataset(id="fake-prompt-hash-test", target="article_analysis")
    case = EvalCase(
        id="fake-prompt-hash-001",
        text="Hello world.",
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )
    run_config = EvalRunConfig(
        run_id="fake-prompt-hash-run-001",
        dataset_id=dataset.id,
        prompt_variant_id="variant-fake",
    )

    await run_eval(
        dataset=dataset,
        cases=[case],
        run_config=run_config,
        adapter=FakeArticleAnalysisAdapterClient(latency_seconds=0.0),
        runs_root=str(tmp_path),
        adapter_run_config={
            "prompt_override": {
                "variant_id": "variant-fake",
                "few_shot_mode": "off",
                "prompt_snapshot_hash": "hash-fake",
            }
        },
    )

    import json

    artifact_path = (
        tmp_path / "fake-prompt-hash-run-001" / "cases" / "fake-prompt-hash-001.json"
    )
    data = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert data["prompt_identity"]["prompt_snapshot_hash"] == "hash-fake"
