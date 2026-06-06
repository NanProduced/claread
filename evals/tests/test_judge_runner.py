from __future__ import annotations

import json
import urllib.error
import urllib.request
from io import BytesIO
from pathlib import Path

import pytest

from claread_eval.judge.adapters import JudgeAdapterError, OpenAICompatibleJudgeAdapterClient
from claread_eval.judge.packet_builder import build_run_rubric_inputs
from claread_eval.judge.runner import JudgeArtifactWriteError, JudgeRunConfig, run_judge
from claread_eval.schemas.rubric import load_rubric


def _write_rubric(evals_root: Path) -> None:
    rubric_dir = evals_root / "rubrics"
    rubric_dir.mkdir(parents=True)
    (rubric_dir / "language-quality-v1.yaml").write_text(
        "\n".join(
            [
                "id: language-quality-v1",
                "version: v1",
                "target: article_analysis",
                "description: Language quality smoke rubric.",
                "criteria:",
                "  - id: clarity",
                "    label: Clarity",
                "    description: The explanation is clear and useful.",
                "    score_min: 1",
                "    score_max: 5",
                "    pass_score: 4",
                "    weight: 1.0",
            ]
        ),
        encoding="utf-8",
    )


def _write_run(evals_root: Path, *, run_id: str = "judge-source-run") -> Path:
    run_dir = evals_root / "runs" / run_id
    cases_dir = run_dir / "cases"
    cases_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(
        json.dumps({"run_id": run_id, "dataset_id": "article-analysis-v1"}),
        encoding="utf-8",
    )
    (run_dir / "report.json").write_text(
        json.dumps({"run_id": run_id, "total_cases": 1, "passed": 1}),
        encoding="utf-8",
    )
    (run_dir / "case-index.json").write_text(
        json.dumps({"run_id": run_id, "total_cases": 1, "cases": [{"case_id": "case-001"}]}),
        encoding="utf-8",
    )
    (cases_dir / "case-001.json").write_text(
        json.dumps(
            {
                "case_id": "case-001",
                "run_id": run_id,
                "adapter_status": "succeeded",
                "input_snapshot": {
                    "text": "The quick brown fox jumps over the lazy dog.",
                    "reading_goal": "daily_reading",
                    "reading_variant": "intermediate_reading",
                },
                "sentence_entries": [{"sentence_id": "s1", "text": "The quick brown fox."}],
                "translations": [{"sentence_id": "s1", "translation": "敏捷的棕色狐狸。"}],
                "inline_marks": [],
                "warnings": [],
                "drop_log": [],
                "prompt_identity": {"prompt_variant_id": "baseline"},
                "model_identity": {"provider": "fake", "model_name": "fake-model"},
            }
        ),
        encoding="utf-8",
    )
    return run_dir


def _write_second_case(run_dir: Path) -> None:
    (run_dir / "cases" / "case-002.json").write_text(
        json.dumps(
            {
                "case_id": "case-002",
                "run_id": run_dir.name,
                "adapter_status": "succeeded",
                "input_snapshot": {
                    "text": "A second short article.",
                    "reading_goal": "daily_reading",
                    "reading_variant": "intermediate_reading",
                },
                "sentence_entries": [{"sentence_id": "s1", "text": "A second short article."}],
                "translations": [{"sentence_id": "s1", "translation": "第二篇短文。"}],
                "inline_marks": [],
                "warnings": [],
                "drop_log": [],
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_fake_judge_writes_deterministic_artifacts(tmp_path: Path) -> None:
    evals_root = tmp_path / "evals"
    _write_rubric(evals_root)
    _write_run(evals_root)

    report, artifact_dir = await run_judge(
        JudgeRunConfig(
            judge_run_id="judge-001",
            run_id="judge-source-run",
            rubric_id="language-quality-v1",
            rubric_version="v1",
            judge_adapter_kind="fake",
        ),
        evals_root=evals_root,
    )

    assert report.total_cases == 1
    assert report.passed == 1
    assert report.failed == 0
    assert (artifact_dir / "judge-run.json").is_file()
    assert (artifact_dir / "packets" / "case-001.json").is_file()
    assert (artifact_dir / "case-results.json").is_file()
    assert (artifact_dir / "report.json").is_file()
    assert (artifact_dir / "report.md").is_file()


@pytest.mark.asyncio
async def test_judge_artifact_directory_is_immutable(tmp_path: Path) -> None:
    evals_root = tmp_path / "evals"
    _write_rubric(evals_root)
    run_dir = _write_run(evals_root)
    existing = run_dir / "judge" / "judge-existing"
    existing.mkdir(parents=True)
    marker = existing / "marker.txt"
    marker.write_text("preserve", encoding="utf-8")

    with pytest.raises(JudgeArtifactWriteError):
        await run_judge(
            JudgeRunConfig(
                judge_run_id="judge-existing",
                run_id="judge-source-run",
                rubric_id="language-quality-v1",
                judge_adapter_kind="fake",
            ),
            evals_root=evals_root,
        )

    assert marker.read_text(encoding="utf-8") == "preserve"


@pytest.mark.asyncio
async def test_judge_runner_respects_max_cases_limit(tmp_path: Path) -> None:
    evals_root = tmp_path / "evals"
    _write_rubric(evals_root)
    run_dir = _write_run(evals_root)
    _write_second_case(run_dir)

    report, artifact_dir = await run_judge(
        JudgeRunConfig(
            judge_run_id="judge-limited",
            run_id="judge-source-run",
            rubric_id="language-quality-v1",
            judge_adapter_kind="fake",
            config_json={"max_cases": 1},
        ),
        evals_root=evals_root,
    )

    payload = json.loads((artifact_dir / "case-results.json").read_text(encoding="utf-8"))
    assert report.total_cases == 1
    assert report.notes[-1] == "Judge run was limited to 1 of 2 cases by max_cases."
    assert [item["case_id"] for item in payload["cases"]] == ["case-001"]
    assert (artifact_dir / "packets" / "case-001.json").is_file()
    assert not (artifact_dir / "packets" / "case-002.json").exists()


@pytest.mark.asyncio
async def test_openai_compatible_adapter_parses_mocked_response(
    tmp_path: Path,
    monkeypatch,
) -> None:
    evals_root = tmp_path / "evals"
    _write_rubric(evals_root)
    run_dir = _write_run(evals_root)
    rubric = load_rubric(evals_root / "rubrics" / "language-quality-v1.yaml")
    packet = build_run_rubric_inputs(rubric=rubric, run_dir=run_dir)[0]

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "verdict": "pass",
                                        "overall_score": 5,
                                        "summary": "clear",
                                        "criteria": [
                                            {
                                                "criterion_id": "clarity",
                                                "score": 5,
                                                "passed": True,
                                                "reason": "clear",
                                            }
                                        ],
                                    }
                                )
                            }
                        }
                    ]
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout):
        assert request.full_url == "https://judge.local/v1/chat/completions"
        assert request.get_header("Authorization") == "Bearer test-key"
        assert timeout == 12
        return FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    adapter = OpenAICompatibleJudgeAdapterClient(
        base_url="https://judge.local/v1",
        api_key="test-key",
        model="judge-model",
        timeout_seconds=12,
    )

    result = await adapter.judge_case(packet)

    assert result.verdict == "pass"
    assert result.overall_score == 5
    assert result.criteria[0].criterion_id == "clarity"


def test_openai_compatible_adapter_requires_https_for_non_local_base_url() -> None:
    with pytest.raises(RuntimeError, match="must use https"):
        OpenAICompatibleJudgeAdapterClient(
            base_url="http://judge.example.com/v1",
            api_key="test-key",
            model="judge-model",
        )


@pytest.mark.asyncio
async def test_openai_compatible_adapter_redacts_http_error_body(
    tmp_path: Path,
    monkeypatch,
) -> None:
    evals_root = tmp_path / "evals"
    _write_rubric(evals_root)
    run_dir = _write_run(evals_root)
    rubric = load_rubric(evals_root / "rubrics" / "language-quality-v1.yaml")
    packet = build_run_rubric_inputs(rubric=rubric, run_dir=run_dir)[0]

    def fake_urlopen(request, timeout):
        del request, timeout
        raise urllib.error.HTTPError(
            url="https://judge.local/v1/chat/completions",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=BytesIO(b'{"error":"bad key","api_key":"sk-secret123456"}'),
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    adapter = OpenAICompatibleJudgeAdapterClient(
        base_url="https://judge.local/v1",
        api_key="test-key",
        model="judge-model",
    )

    with pytest.raises(JudgeAdapterError) as exc_info:
        await adapter.judge_case(packet)

    message = str(exc_info.value)
    assert "sk-secret123456" not in message
    assert "<redacted>" in message


@pytest.mark.asyncio
async def test_malformed_judge_case_marks_case_error_without_failing_artifact(
    tmp_path: Path,
    monkeypatch,
) -> None:
    evals_root = tmp_path / "evals"
    _write_rubric(evals_root)
    _write_run(evals_root)

    class BrokenAdapter:
        adapter_kind = "llm"

        async def judge_case(self, packet):
            raise ValueError("malformed judge JSON")

    monkeypatch.setattr(
        "claread_eval.judge.runner.create_judge_adapter",
        lambda *args, **kwargs: BrokenAdapter(),
    )

    report, artifact_dir = await run_judge(
        JudgeRunConfig(
            judge_run_id="judge-broken",
            run_id="judge-source-run",
            rubric_id="language-quality-v1",
            judge_adapter_kind="llm",
        ),
        evals_root=evals_root,
    )

    payload = json.loads((artifact_dir / "case-results.json").read_text(encoding="utf-8"))
    assert report.errored == 1
    assert payload["cases"][0]["status"] == "error"
    assert payload["cases"][0]["verdict"] == "error"
